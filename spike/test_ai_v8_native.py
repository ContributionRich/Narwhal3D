#!/usr/bin/env python3
"""Round07 compiled display-projection mutants in external copied crates only."""
import os
from test_ai_accepted_review_native import TARGET
import test_ai_v4_native as driver

if __name__ == '__main__':
    os.environ['NARWHAL_AI_BUILD_MIB'] = '1024'
    driver.MUTATIONS = [
        ('v8_display_boundary', 'conversation.rs', '.nth(DISPLAY_CHARS)',
         '.nth(DISPLAY_CHARS + 1)', 'v8_display_projection'),
        ('v8_display_notice', 'conversation.rs', 'let notice = truncated.then_some(',
         'let notice = false.then_some(', 'v8_display_projection'),
    ]
    driver.run(TARGET.resolve(), 'v8_display_projection', True)
