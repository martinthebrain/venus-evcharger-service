use proptest::prelude::*;
use venus_evcharger_forensic_observer::artifact::{mounted_storage_candidates, redact_config_text};
use venus_evcharger_forensic_observer::ini::IniDocument;
use venus_evcharger_forensic_observer::snapshot::slug_text;

proptest! {
    #[test]
    fn incident_slugs_are_nonempty_safe_path_components(input in any::<String>()) {
        let slug = slug_text(&input);
        prop_assert!(!slug.is_empty());
        let contains_only_safe_characters = slug.chars().all(|character| {
            character.is_ascii_lowercase() || character.is_ascii_digit() || character == '-'
        });
        prop_assert!(contains_only_safe_characters);
        prop_assert!(!slug.starts_with('-'));
        prop_assert!(!slug.ends_with('-'));
        prop_assert!(!slug.contains("--"));
    }

    #[test]
    fn every_token_assignment_is_redacted(value in "[A-Za-z0-9]{1,64}") {
        let secret = format!("private-{value}");
        let redacted = redact_config_text(&format!("ControlApiAdminToken={secret}\n"));
        prop_assert_eq!(redacted, "ControlApiAdminToken=<redacted>\n");
    }

    #[test]
    fn removable_mount_decoding_preserves_safe_names(name in "[A-Za-z0-9_-]{1,48}") {
        let input = format!("/dev/sda1 /media/{name} ext4 rw 0 0\n");
        let candidates = mounted_storage_candidates(&input);
        prop_assert_eq!(candidates.len(), 1);
        prop_assert_eq!(candidates[0].to_string_lossy(), format!("/media/{name}"));
    }

    #[test]
    fn arbitrary_ini_text_never_panics(input in any::<String>()) {
        let _result = IniDocument::parse(&input);
    }
}
