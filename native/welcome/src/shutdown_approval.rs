//! Deferred host shutdown is not update approval. Cancellation wins if both
//! markers are observed; only an approved successful process exit permits apply.
#[derive(Debug, PartialEq, Eq)]
pub enum Decision {
    Waiting,
    Declined,
    Apply,
    UnapprovedExit,
}
pub fn observe(requested: bool, approved: bool, declined: bool, exit: Option<i32>) -> Decision {
    if declined && requested {
        return Decision::Declined;
    }
    match exit {
        None => Decision::Waiting,
        Some(0) if requested && approved && !declined => Decision::Apply,
        Some(_) => Decision::UnapprovedExit,
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn pending_and_saving_never_approve_or_timeout_into_apply() {
        for _ in 0..1000 {
            assert_eq!(observe(true, false, false, None), Decision::Waiting);
            assert_eq!(observe(true, true, false, None), Decision::Waiting);
        }
        assert_eq!(observe(true, true, false, Some(0)), Decision::Apply);
    }
    #[test]
    fn cancel_failure_and_unrequested_exits_cannot_apply() {
        assert_eq!(observe(true, true, true, Some(0)), Decision::Declined);
        assert_eq!(observe(true, false, true, None), Decision::Declined);
        assert_eq!(
            observe(true, true, false, Some(1)),
            Decision::UnapprovedExit
        );
        assert_eq!(
            observe(false, true, false, Some(0)),
            Decision::UnapprovedExit
        );
        assert_eq!(
            observe(true, false, false, Some(0)),
            Decision::UnapprovedExit
        );
    }
}
