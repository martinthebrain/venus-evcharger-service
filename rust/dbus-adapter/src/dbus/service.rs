// SPDX-License-Identifier: GPL-3.0-or-later
//! Dynamic `com.victronenergy.BusItem` service implementation.

use std::collections::{BTreeMap, BTreeSet, HashMap};
use std::sync::{Arc, RwLock};

use zbus::blocking::Connection;
use zbus::interface;
use zbus::object_server::SignalEmitter;
use zbus::zvariant::OwnedValue;

use super::{BusValue, TextFormat};

pub type WriteHandler = Arc<dyn Fn(&str, &BusValue) -> bool + Send + Sync>;

#[derive(Clone)]
struct PublishedPath {
    value: BusValue,
    format: TextFormat,
    writable: bool,
}

type SharedPaths = Arc<RwLock<BTreeMap<String, PublishedPath>>>;

#[derive(Clone)]
struct BusItem {
    path: String,
    paths: SharedPaths,
    write_handler: Option<WriteHandler>,
}

#[interface(name = "com.victronenergy.BusItem", spawn = false)]
impl BusItem {
    fn get_value(&self) -> zbus::fdo::Result<OwnedValue> {
        self.path_value()?
            .value
            .to_owned()
            .map_err(zbus::fdo::Error::Failed)
    }

    fn get_text(&self) -> zbus::fdo::Result<String> {
        let item = self.path_value()?;
        Ok(item.value.text(&self.path, item.format))
    }

    // Venus requires both arguments although its canonical implementation
    // returns the same description for every requested language and length.
    #[allow(clippy::unused_self, clippy::no_effect_underscore_binding)]
    fn get_description(&self, language: &str, length: i32) -> String {
        let _requested_format = (language, length);
        "No description given".to_owned()
    }

    #[allow(clippy::needless_pass_by_value)]
    fn set_value(&self, new_value: OwnedValue) -> i32 {
        let Ok(item) = self.path_value() else {
            return 1;
        };
        if !item.writable {
            return 1;
        }
        let Ok(value) = BusValue::from_owned(&new_value) else {
            return 1;
        };
        if value == item.value {
            return 0;
        }
        match &self.write_handler {
            Some(handler) if handler(&self.path, &value) => 0,
            Some(_) => 2,
            None => 1,
        }
    }

    #[zbus(signal)]
    async fn properties_changed(
        emitter: &SignalEmitter<'_>,
        changes: HashMap<String, OwnedValue>,
    ) -> zbus::Result<()>;
}

impl BusItem {
    fn path_value(&self) -> zbus::fdo::Result<PublishedPath> {
        self.paths
            .read()
            .map_err(|_| zbus::fdo::Error::Failed("publication state poisoned".to_owned()))?
            .get(&self.path)
            .cloned()
            .ok_or_else(|| zbus::fdo::Error::UnknownObject(self.path.clone()))
    }
}

#[derive(Clone)]
struct BusItemTree {
    path: String,
    paths: SharedPaths,
}

#[interface(name = "com.victronenergy.BusItem", spawn = false)]
impl BusItemTree {
    fn get_value(&self) -> zbus::fdo::Result<OwnedValue> {
        self.tree_values(false)
    }

    fn get_text(&self) -> zbus::fdo::Result<OwnedValue> {
        self.tree_values(true)
    }
}

impl BusItemTree {
    fn tree_values(&self, text: bool) -> zbus::fdo::Result<OwnedValue> {
        let prefix = format!("{}/", self.path.trim_end_matches('/'));
        let values = self
            .paths
            .read()
            .map_err(|_| zbus::fdo::Error::Failed("publication state poisoned".to_owned()))?;
        let mut result = HashMap::new();
        for (path, item) in values.iter().filter(|(path, _)| path.starts_with(&prefix)) {
            let relative = path.trim_start_matches(&prefix).to_owned();
            let value = if text {
                BusValue::Text(item.value.text(path, item.format)).to_owned()
            } else {
                item.value.to_owned()
            }
            .map_err(zbus::fdo::Error::Failed)?;
            result.insert(relative, value);
        }
        drop(values);
        Ok(OwnedValue::from(result))
    }
}

#[derive(Clone)]
struct BusItemRoot {
    paths: SharedPaths,
}

#[interface(name = "com.victronenergy.BusItem", spawn = false)]
impl BusItemRoot {
    fn get_value(&self) -> zbus::fdo::Result<OwnedValue> {
        BusItemTree {
            path: String::new(),
            paths: self.paths.clone(),
        }
        .tree_values(false)
    }

    fn get_text(&self) -> zbus::fdo::Result<OwnedValue> {
        BusItemTree {
            path: String::new(),
            paths: self.paths.clone(),
        }
        .tree_values(true)
    }

    fn get_items(&self) -> zbus::fdo::Result<HashMap<String, HashMap<String, OwnedValue>>> {
        let values = self
            .paths
            .read()
            .map_err(|_| zbus::fdo::Error::Failed("publication state poisoned".to_owned()))?;
        let mut result = HashMap::new();
        for (path, item) in &*values {
            let mut fields = HashMap::new();
            fields.insert(
                "Value".to_owned(),
                item.value.to_owned().map_err(zbus::fdo::Error::Failed)?,
            );
            fields.insert(
                "Text".to_owned(),
                BusValue::Text(item.value.text(path, item.format))
                    .to_owned()
                    .map_err(zbus::fdo::Error::Failed)?,
            );
            result.insert(path.clone(), fields);
        }
        drop(values);
        Ok(result)
    }

    #[zbus(signal)]
    async fn items_changed(
        emitter: &SignalEmitter<'_>,
        changes: HashMap<String, HashMap<String, OwnedValue>>,
    ) -> zbus::Result<()>;
}

pub struct PublishedService {
    connection: Connection,
    service_name: String,
    paths: SharedPaths,
}

impl PublishedService {
    pub fn register(
        service_name: &str,
        initial: impl IntoIterator<Item = (String, BusValue, TextFormat, bool)>,
        write_handler: Option<&WriteHandler>,
    ) -> Result<Self, String> {
        let connection = Connection::system().map_err(|error| error.to_string())?;
        let paths: SharedPaths = Arc::new(RwLock::new(BTreeMap::new()));
        {
            let mut values = paths
                .write()
                .map_err(|_| "publication state poisoned".to_owned())?;
            for (path, value, format, writable) in initial {
                values.insert(
                    path,
                    PublishedPath {
                        value,
                        format,
                        writable,
                    },
                );
            }
        }
        connection
            .object_server()
            .at(
                "/",
                BusItemRoot {
                    paths: paths.clone(),
                },
            )
            .map_err(|error| error.to_string())?;

        let path_names = paths
            .read()
            .map_err(|_| "publication state poisoned".to_owned())?
            .keys()
            .cloned()
            .collect::<Vec<_>>();
        for tree in intermediate_paths(&path_names) {
            connection
                .object_server()
                .at(
                    tree.clone(),
                    BusItemTree {
                        path: tree,
                        paths: paths.clone(),
                    },
                )
                .map_err(|error| error.to_string())?;
        }
        for path in path_names {
            connection
                .object_server()
                .at(
                    path.clone(),
                    BusItem {
                        path,
                        paths: paths.clone(),
                        write_handler: write_handler.cloned(),
                    },
                )
                .map_err(|error| error.to_string())?;
        }
        connection
            .request_name(service_name)
            .map_err(|error| error.to_string())?;
        Ok(Self {
            connection,
            service_name: service_name.to_owned(),
            paths,
        })
    }

    pub fn publish(&self, path: &str, value: &BusValue) -> Result<bool, String> {
        let changed = {
            let mut paths = self
                .paths
                .write()
                .map_err(|_| "publication state poisoned".to_owned())?;
            let item = paths
                .get_mut(path)
                .ok_or_else(|| format!("unknown publication path: {path}"))?;
            let changed = if item.value == *value {
                false
            } else {
                item.value = value.clone();
                true
            };
            drop(paths);
            changed
        };
        if !changed {
            return Ok(false);
        }
        let item = self.path(path)?;
        let mut field_changes = HashMap::new();
        field_changes.insert("Value".to_owned(), value.to_owned()?);
        field_changes.insert(
            "Text".to_owned(),
            BusValue::Text(value.text(path, item.format)).to_owned()?,
        );
        self.connection
            .emit_signal(
                None::<&str>,
                path,
                "com.victronenergy.BusItem",
                "PropertiesChanged",
                &field_changes,
            )
            .map_err(|error| error.to_string())?;
        Ok(true)
    }

    pub fn service_name(&self) -> &str {
        &self.service_name
    }

    fn path(&self, path: &str) -> Result<PublishedPath, String> {
        self.paths
            .read()
            .map_err(|_| "publication state poisoned".to_owned())?
            .get(path)
            .cloned()
            .ok_or_else(|| format!("unknown publication path: {path}"))
    }
}

fn intermediate_paths(paths: &[String]) -> Vec<String> {
    let mut result = BTreeSet::new();
    for path in paths {
        let components = path.split('/').filter(|component| !component.is_empty());
        let mut current = String::new();
        let components = components.collect::<Vec<_>>();
        for component in components.iter().take(components.len().saturating_sub(1)) {
            current.push('/');
            current.push_str(component);
            result.insert(current.clone());
        }
    }
    result.into_iter().collect()
}

#[cfg(test)]
mod tests {
    use super::intermediate_paths;

    #[test]
    fn intermediate_tree_paths_are_unique_and_ordered() {
        let paths = vec![
            "/Ac/L1/Power".to_owned(),
            "/Ac/L2/Power".to_owned(),
            "/Mgmt/ProcessName".to_owned(),
        ];
        assert_eq!(
            intermediate_paths(&paths),
            vec!["/Ac", "/Ac/L1", "/Ac/L2", "/Mgmt"],
        );
    }
}
