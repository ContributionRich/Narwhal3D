//! TEST-ONLY cached measurement prototype. No ABI or production activation.
//! A caller must attest pure measurements and supply a complete font/layout epoch.
//! Current public Python helpers provide neither; see README.md before integrating.
use std::collections::BTreeMap;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
struct Epoch {
    font_instance: u64,
    font_revision: u64,
    size_bits: u64,
    style_revision: u64,
    text_revision: u64,
    width_bits: u64,
}
struct Metrics {
    epoch: Epoch,
    values: BTreeMap<Vec<u32>, f64>,
    failed: bool,
}
impl Metrics {
    fn new(epoch: Epoch) -> Self {
        Self {
            epoch,
            values: BTreeMap::new(),
            failed: false,
        }
    }
    fn epoch(&mut self, epoch: Epoch) {
        if self.epoch != epoch {
            self.values.clear();
            self.failed = false;
            self.epoch = epoch;
        }
    }
    fn measure(
        &mut self,
        text: &[u32],
        mut provider: impl FnMut(&[u32]) -> Result<f64, ()>,
    ) -> Result<f64, ()> {
        if self.failed {
            return Err(());
        }
        if let Some(value) = self.values.get(text) {
            return Ok(*value);
        }
        match provider(text) {
            Ok(value) => {
                // Prototype memory bound, not a text/layout acceptance limit.
                if self.values.len() < 256 && text.len() <= 4096 {
                    self.values.insert(text.to_vec(), value);
                }
                Ok(value)
            }
            Err(()) => {
                self.failed = true;
                Err(())
            }
        }
    }
}
fn epoch() -> Epoch {
    Epoch {
        font_instance: 1,
        font_revision: 1,
        size_bits: 12f64.to_bits(),
        style_revision: 1,
        text_revision: 1,
        width_bits: 45f64.to_bits(),
    }
}
fn kerned(text: &[u32]) -> f64 {
    text.len() as f64 * 10.0 - text.windows(2).filter(|s| *s == [65, 86]).count() as f64 * 9.0
}
#[test]
fn exact_substring_metrics_not_additive_glyphs() {
    use super::layout_algorithm::*;
    let text: Vec<_> = "AV AV\n\nAVVV AV".chars().map(u32::from).collect();
    assert_ne!(kerned(&[65, 86]), kerned(&[65]) + kerned(&[86]));
    let mut measured = 0;
    for width in [0.0f64, 11.0, 45.0, -1.0, f64::NAN] {
        let mut current = epoch();
        current.width_bits = width.to_bits();
        let mut metrics = Metrics::new(current);
        let mut cached = |s: &[u32]| {
            metrics
                .measure(s, |s| {
                    measured += 1;
                    Ok(kerned(s))
                })
                .unwrap()
        };
        let expected = wrap_spans(&text, width, &mut kerned);
        let actual = wrap_spans(&text, width, &mut cached);
        assert_eq!(actual, expected);
        if let Ok(spans) = expected {
            for index in 0..=text.len() {
                assert_eq!(
                    caret_xy(&spans, &text, index as i64, &mut cached),
                    caret_xy(&spans, &text, index as i64, &mut kerned)
                );
            }
            for line in 0..spans.len() {
                for x in [0.0, 5.0, 11.0, 100.0] {
                    assert_eq!(
                        index_at(&spans, &text, x, line as i64, &mut cached),
                        index_at(&spans, &text, x, line as i64, &mut kerned)
                    );
                }
            }
            assert_eq!(
                selection_rects(&spans, &text, Some((1, text.len() as i64)), &mut cached),
                selection_rects(&spans, &text, Some((1, text.len() as i64)), &mut kerned)
            );
        }
    }
    assert!(measured > 0);
}
#[test]
fn every_epoch_component_invalidates_and_failures_do_not_repeat_callbacks() {
    let mut cache = Metrics::new(epoch());
    let mut e = epoch();
    for component in 0..6 {
        assert_eq!(cache.measure(&[65, 86], |_| Ok(11.0)), Ok(11.0));
        assert_eq!(cache.measure(&[65, 86], |_| panic!("cached")), Ok(11.0));
        match component {
            0 => e.font_instance += 1,
            1 => e.font_revision += 1,
            2 => e.size_bits += 1,
            3 => e.style_revision += 1,
            4 => e.text_revision += 1,
            _ => e.width_bits += 1,
        }
        cache.epoch(e);
        assert!(cache.values.is_empty());
    }
    let mut calls = 0;
    assert_eq!(
        cache.measure(&[65], |_| {
            calls += 1;
            Err(())
        }),
        Err(())
    );
    assert_eq!(
        cache.measure(&[65], |_| {
            calls += 1;
            Ok(1.0)
        }),
        Err(())
    );
    assert_eq!(calls, 1);
    cache.epoch(e);
    assert_eq!(
        cache.measure(&[65], |_| panic!("same failed epoch")),
        Err(())
    );
    e.font_revision += 1;
    cache.epoch(e);
    assert_eq!(cache.measure(&[65], |_| Ok(9.0)), Ok(9.0));
}
