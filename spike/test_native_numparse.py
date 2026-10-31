"""Required-native parser coverage; former fallback policy is retired.

The original suite is preserved in the local retirement archive. Ordinary values
and exact errors remain covered by frozen pre-retirement data, plus direct ABI,
missing/old-core refusal, corrupt replies and exact large-rational rounding.
"""
import unittest
from test_numparse_owned import OwnedParserTests

if __name__ == '__main__':
    unittest.main(verbosity=2)
