//! Disposable MySQL and PostgreSQL storage integration tests.
use rcfp_core::storage::{Dataset, KeyDefinition, Store};
#[test]
fn transaction_unwind_and_savepoint_recovery() {
    for variable in ["RCFP_TEST_MYSQL_URL", "RCFP_TEST_POSTGRESQL_URL"] {
        let base = std::env::var(variable)
            .unwrap_or_else(|_| panic!("set {variable} to a disposable database server URL"));
        let name = format!(
            "rcfp_unwind_{}_{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        );
        let store = Store::create(
            &format!("{base}/{name}"),
            None,
            &[KeyDefinition {
                key: "key1".into(),
                description: String::new(),
            }],
        )
        .unwrap();
        let d = Dataset {
            keys: vec!["first".into()],
            path: "s3://test/first.tif".into(),
        };
        store.insert(&d, None).unwrap();
        let panic = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
            let _: Result<(), String> = store.transaction(|s| {
                s.delete(&d.keys)?;
                panic!("injected unwind");
            });
        }));
        assert!(panic.is_err());
        assert!(store.dataset(&d.keys).unwrap().is_some());
        store
            .transaction(|s| {
                let failed: Result<(), String> = s.transaction(|nested| {
                    nested.delete(&d.keys)?;
                    Err("injected nested failure".into())
                });
                assert!(failed.is_err());
                assert!(s.dataset(&d.keys)?.is_some());
                s.insert(
                    &Dataset {
                        keys: vec!["second".into()],
                        path: "new".into(),
                    },
                    None,
                )
            })
            .unwrap();
        assert_eq!(store.datasets().unwrap().len(), 2);
        store.delete(&d.keys).unwrap();
        assert_eq!(store.datasets().unwrap().len(), 1);
    }
}
