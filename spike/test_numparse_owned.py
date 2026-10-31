"""Native-only parser contract, frozen pre-retirement data and exact rational checks.

Set NARWHAL_PURE_CORE to a source-built library; no implicit build or fallback.
Python is ABI/test orchestration only. The old implementation is not imported.
"""
import ctypes
from fractions import Fraction
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import random
import struct
import sys
import unittest
import unicodedata
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('owned_numparse',ROOT/'narwhal3d/numparse.py')
parser=importlib.util.module_from_spec(spec);spec.loader.exec_module(parser)
native=parser._native_pure()
FIXTURE=ROOT/'spike/fixtures/numparse_golden.json'
DIGEST='9016730b186ee2f394449b0eaa0c6761c7b5b101b66e89331f005ca0ab2203dd'


class OwnedParserTests(unittest.TestCase):
    def test_frozen_pre_retirement_results(self):
        data=FIXTURE.read_bytes()
        self.assertEqual(hashlib.sha256(data).hexdigest(),DIGEST)
        rows=json.loads(data)[unicodedata.unidata_version]
        before=native._native_numparse_calls
        for text,model,want in rows:
            got=parser.parse_number(text) if model is None else parser.parse_length(text,model)
            self.assertEqual(list(got),want,(text,model,got,want))
        self.assertEqual(native._native_numparse_calls-before,len(rows),'errors must also execute Rust')

    def test_exact_large_fraction_rounding(self):
        rng=random.Random(8072)
        for _ in range(500):
            a=rng.getrandbits(rng.randrange(1,1800))
            b=rng.getrandbits(rng.randrange(1,1800)) or 1
            try: want=float(Fraction(a,b))
            except OverflowError: continue
            value,error=parser.parse_number(f'{a}/{b}')
            self.assertIsNone(error)
            self.assertEqual(struct.pack('d',value),struct.pack('d',want),(a,b,value,want))
        for a,b in ((2**53+1,2**53),(2**53+3,2**53),(1,2**1075),(3,2**1075),(1,2**1074)):
            value,error=parser.parse_number(f'{a}/{b}')
            self.assertIsNone(error)
            self.assertEqual(value,float(Fraction(a,b)))

    def test_invalid_inputs_and_finite_policy(self):
        self.assertEqual(parser.parse_number(None),(None,'expected a string'))
        self.assertEqual(parser.parse_length('1',None),(None,'expected a model unit string'))
        for text in ('1e999','9'*400,'1e308km'):
            value,error=parser.parse_length(text)
            self.assertIsNone(value)
            self.assertIn('finite range',error)
        self.assertIn('4096',parser.parse_number('1'*4097)[1])
        self.assertEqual(math.copysign(1,parser.parse_number('-0')[0]),-1)
        self.assertEqual(parser.parse_length('', 'bad'),(None,"unknown model unit: 'bad'"))
        self.assertEqual(parser.parse_length(' ', 'bad'),(None,'no spaces are permitted in a number'))
        self.assertEqual(parser.parse_length(' ', '\ud800'),(None,'no spaces are permitted in a number'))
        self.assertEqual(parser.parse_length('1', '\ud800'),(None,"unknown model unit: '\\ud800'"))

    def test_backend_is_required_even_for_invalid_text(self):
        with patch.dict(os.environ,{'NARWHAL_PURE_CORE':str(ROOT/'nonexistent-parser-library')}):
            with self.assertRaisesRegex(RuntimeError,'Required Rust number parser'):
                parser.parse_number('not a number')
        with patch.dict(os.environ,{'NARWHAL_PURE_CORE':''}):
            with self.assertRaises(RuntimeError): parser.parse_number('1')

    def test_old_abi_and_missing_export_are_installation_errors(self):
        class Function:
            def __init__(self,value):self.value=value
            def __call__(self):return self.value
        for fake in (type('Old',(),{'narwhal_pure_core_parse_abi':Function(1)})(),object()):
            with patch.object(native,'_owned_parse_binding',None),patch.object(native.ctypes,'CDLL',return_value=fake):
                with self.assertRaises(RuntimeError): parser.parse_number('1')

    def test_malformed_reply_never_becomes_a_number(self):
        cases=({'code':123},{'reserved':1},{'value':float('nan')},{'code':-2,'value':5},
               {'code':-4,'start':2,'end':1},{'code':-2,'end':100},{'start':1})
        for fields in cases:
            def corrupt(*args):
                args[-2]._obj.code = 0
                for name,value in fields.items():setattr(args[-2]._obj,name,value)
                return 0
            with patch.object(native,'_required_parse_binding',return_value=corrupt):
                with self.assertRaisesRegex(RuntimeError,'Invalid Rust'):
                    parser.parse_number('1')
        for rc in (0, 1):
            with patch.object(native,'_required_parse_binding',return_value=lambda *args:rc):
                with self.assertRaises(RuntimeError):parser.parse_number('1')

    def test_direct_ffi_bounds_and_output(self):
        fn=native._required_parse_binding()
        out=native.ParseReply(value=7)
        self.assertEqual(ctypes.sizeof(out),24)
        self.assertEqual(fn(None,1,None,0,0,160,ctypes.byref(out),24),1)
        self.assertEqual(out.value,0)
        self.assertEqual(fn(None,4097,None,0,0,160,ctypes.byref(out),24),0)
        self.assertEqual(out.code,-7)
        self.assertEqual(fn(None,0,None,0,0,999,ctypes.byref(out),24),2)
        self.assertEqual(fn(None,0,None,0,0,160,ctypes.byref(out),23),1)

    def test_no_python_parser_or_alternate_authority(self):
        source=(ROOT/'narwhal3d/numparse.py').read_text()
        for marker in ('_BARE','_bare_value','_LENGTH_RE','import re','legacy','fallback'):
            self.assertNotIn(marker,source)
        with patch.object(native,'parse_owned',side_effect=RuntimeError('sentinel')):
            with self.assertRaisesRegex(RuntimeError,'sentinel'):parser.parse_number('1')


if __name__=='__main__':unittest.main(verbosity=2)
