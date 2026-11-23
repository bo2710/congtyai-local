import json
import unittest

from office import contracts
from office.continuation import Prefix


class FieldLimits(unittest.TestCase):
    def test_partial_qa_value(self):
        schema=contracts.schema_for('blind')
        prefix=Prefix(schema)
        prefix.add('{"browser_cases":[{"steps":[{"action":"fill","selector":"body","value":"')
        with self.assertRaises(contracts.ReplyError):
            prefix.add('<html>'+'x'*700)

    def test_unicode_boundary(self):
        prefix=Prefix({'properties':{'text':{'type':'string','maxLength':1}}})
        prefix.add('{"text":"'+r'\ud83d')
        self.assertEqual(prefix.add(r'\ude00"}'),{'text':'😀'})

    def test_long_product(self):
        content='<div>test</div>'*3000
        body=json.dumps({'text':'Bàn giao','files':[{'name':'index.html','content':content}]})
        schema=contracts.schema_for('deliver')
        prefix=Prefix(schema)
        for start in range(0,len(body),701):
            result=prefix.add(body[start:start+701])
        contracts.validate(result,schema)
        self.assertEqual(result['files'][0]['content'],content)

    def test_review_metadata(self):
        prefix=Prefix(contracts.schema_for('plan_review'))
        with self.assertRaises(contracts.ReplyError):
            prefix.add('{"verdict":"'+('A'*1000))

    def test_sql_array_bound(self):
        prefix=Prefix(contracts.schema_for('blind'))
        with self.assertRaises(contracts.ReplyError):
            prefix.add('{"sql_cases":[{"rows":['+'[1,null],'*64)

    def test_repeating_review_prose(self):
        with self.assertRaises(contracts.ReplyError):
            Prefix(contracts.schema_for('quality')).add('{"text":"'+'.seat{min-height:52px}'*80)
