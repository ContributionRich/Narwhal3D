#!/usr/bin/env python3
"""Focused compiled clock-control mutation; normal runtime has no clock setter."""
import os
from pathlib import Path
import test_ai_v4_native as driver

if __name__=='__main__':
    os.environ['NARWHAL_AI_BUILD_MIB']='1024'
    driver.MUTATIONS=[('visible_clock_bound','conversation.rs','if delta_ms > 3_600_000 {','if delta_ms > 3_600_001 {','v7_review_clock')]
    driver.run(Path('/tmp/narwhal3d-ai-sustained-v4'),'v7_review_clock',True)
