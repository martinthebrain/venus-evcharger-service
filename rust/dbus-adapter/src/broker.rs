// SPDX-License-Identifier: GPL-3.0-or-later
//! Single-flight worker isolating bounded blocking D-Bus calls from the runtime loop.

use std::sync::mpsc::{Receiver, SyncSender, TryRecvError, TrySendError, sync_channel};
use std::thread::{self, JoinHandle};
use std::time::{Duration, Instant};

use crate::dbus::{BusValue, DbusClient};

#[derive(Clone, Debug)]
pub enum DbusOperation {
    ListNames,
    Read {
        service: String,
        path: String,
    },
    Write {
        service: String,
        path: String,
        value: BusValue,
    },
    Introspect {
        service: String,
        path: String,
    },
}

#[derive(Clone, Debug)]
pub enum DbusResultValue {
    Names(Vec<String>),
    Value(BusValue),
    WriteCode(i32),
    Xml(String),
}

#[derive(Clone, Debug)]
pub struct DbusResult {
    pub operation: DbusOperation,
    pub result: Result<DbusResultValue, String>,
    pub duration: Duration,
}

enum WorkerMessage {
    Execute(DbusOperation),
    Stop,
}

pub struct OperationBroker {
    request: SyncSender<WorkerMessage>,
    response: Receiver<DbusResult>,
    worker: Option<JoinHandle<()>>,
    busy: bool,
}

impl OperationBroker {
    pub fn start(timeout: Duration) -> Result<Self, String> {
        let (request_tx, request_rx) = sync_channel(1);
        let (response_tx, response_rx) = sync_channel(1);
        let worker = thread::Builder::new()
            .name("evcs-dbus".to_owned())
            .spawn(move || worker_loop(timeout, &request_rx, &response_tx))
            .map_err(|error| error.to_string())?;
        Ok(Self {
            request: request_tx,
            response: response_rx,
            worker: Some(worker),
            busy: false,
        })
    }

    pub fn submit(&mut self, operation: DbusOperation) -> Result<bool, String> {
        if self.busy {
            return Ok(false);
        }
        match self.request.try_send(WorkerMessage::Execute(operation)) {
            Ok(()) => {
                self.busy = true;
                Ok(true)
            }
            Err(TrySendError::Full(_)) => Ok(false),
            Err(TrySendError::Disconnected(_)) => Err("D-Bus worker stopped".to_owned()),
        }
    }

    pub fn poll(&mut self) -> Result<Option<DbusResult>, String> {
        match self.response.try_recv() {
            Ok(result) => {
                self.busy = false;
                Ok(Some(result))
            }
            Err(TryRecvError::Empty) => Ok(None),
            Err(TryRecvError::Disconnected) => Err("D-Bus worker stopped".to_owned()),
        }
    }

    pub const fn busy(&self) -> bool {
        self.busy
    }
}

impl Drop for OperationBroker {
    fn drop(&mut self) {
        let _ignored = self.request.send(WorkerMessage::Stop);
        if let Some(worker) = self.worker.take() {
            let _ignored = worker.join();
        }
    }
}

fn worker_loop(
    timeout: Duration,
    requests: &Receiver<WorkerMessage>,
    responses: &SyncSender<DbusResult>,
) {
    let Ok(mut client) = DbusClient::new(timeout) else {
        return;
    };
    while let Ok(message) = requests.recv() {
        let WorkerMessage::Execute(operation) = message else {
            break;
        };
        let started = Instant::now();
        let result = execute(&mut client, &operation);
        if result.is_err() {
            client.reset();
        }
        let response = DbusResult {
            operation,
            result,
            duration: started.elapsed(),
        };
        if responses.send(response).is_err() {
            break;
        }
    }
}

fn execute(client: &mut DbusClient, operation: &DbusOperation) -> Result<DbusResultValue, String> {
    match operation {
        DbusOperation::ListNames => client.list_names().map(DbusResultValue::Names),
        DbusOperation::Read { service, path } => {
            client.read(service, path).map(DbusResultValue::Value)
        }
        DbusOperation::Write {
            service,
            path,
            value,
        } => client
            .write(service, path, value)
            .map(DbusResultValue::WriteCode),
        DbusOperation::Introspect { service, path } => {
            client.introspect(service, path).map(DbusResultValue::Xml)
        }
    }
}

#[cfg(test)]
mod tests {
    use super::OperationBroker;

    #[test]
    fn broker_starts_idle_with_one_bounded_worker() -> Result<(), String> {
        let broker = OperationBroker::start(std::time::Duration::from_millis(50))?;
        assert!(!broker.busy());
        Ok(())
    }
}
