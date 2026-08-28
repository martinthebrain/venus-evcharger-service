// SPDX-License-Identifier: GPL-3.0-or-later
//! PV source validation and explicit dormancy lifecycle.

use std::collections::HashSet;

use super::{EnergyReader, ReadMember};
use crate::energy::Clocks;

impl EnergyReader {
    pub(super) fn record_pv_value(&mut self, member: &ReadMember) {
        let Ok(clocks) = Clocks::now() else {
            return;
        };
        let active = self.active_pv_source_ids();
        self.pv_dormancy
            .record_value(&member.source_id, clocks, &active);
        self.validated_pv
            .insert(member.source_id.clone(), member.clone());
    }

    pub(super) fn record_pv_error(&mut self, member: &ReadMember, error: &str) {
        let Ok(clocks) = Clocks::now() else {
            return;
        };
        let active = self.active_pv_source_ids();
        self.pv_dormancy
            .record_error(&member.source_id, error, clocks, &active);
        if self.pv_dormancy.validated(&member.source_id) {
            self.validated_pv
                .insert(member.source_id.clone(), member.clone());
        }
    }

    pub(super) fn maintain_pv(&mut self) {
        let Ok(clocks) = Clocks::now() else {
            return;
        };
        let active = self.active_pv_source_ids();
        self.pv_dormancy.maintain(&active, clocks.monotonic);
        let retained = self.pv_dormancy.validated_source_ids();
        self.validated_pv
            .retain(|source_id, _member| retained.contains(source_id));
    }

    fn active_pv_source_ids(&self) -> HashSet<String> {
        self.pv_candidate_members()
            .into_iter()
            .map(|member| member.source_id)
            .collect()
    }

    pub(super) fn known_pv_source_ids(&self) -> HashSet<String> {
        self.pv_descriptor_members()
            .into_iter()
            .map(|member| member.source_id)
            .collect()
    }
}
