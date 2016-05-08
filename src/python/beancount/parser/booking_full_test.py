__author__ = "Martin Blais <blais@furius.ca>"

import collections
import datetime
import textwrap
import unittest
import re
import io

from beancount.core.number import D
from beancount.core.amount import A
from beancount.core.number import MISSING
from beancount.core.position import CostSpec
from beancount.core.position import Cost
from beancount.core.position import Position
from beancount.core.inventory import from_string as I
from beancount.utils.misc_utils import dictmap
from beancount.core.data import Booking
from beancount.core import inventory
from beancount.core import position
from beancount.core import amount
from beancount.core import data
from beancount.parser import parser
from beancount.parser import printer
from beancount.parser import booking_full as bf
from beancount.parser import booking_simple
from beancount.parser import cmptest
from beancount import loader


def _gen_missing_combinations(template, args):
    """Generate all possible expansions of args in template.

    Args:
      template: A string, the template in new-sytle formatting.
      args: A list of strings to be included or excluded from the template.
    Yields:
      Strings of formatted template.
    """
    for mask in range(2 ** len(args)):
        actual_args = [arg if not (1<<i & mask) else ''
                       for i, arg in enumerate(args)]
        yield template.format(*actual_args)


class TestAllInterpolationCombinations(cmptest.TestCase):

    def test_all_currency_interpolations(self):
        template = textwrap.dedent("""
          2015-10-02 *
            Assets:Account  {}
            Assets:Other
        """)
        for pos_template, args in [
                ('100.00 {:3}',
                 ['USD']),
                ('100.00 {:3} @ 1.20 {:3}',
                 ['USD', 'CAD']),
                ('10 {:4} {{100.00 {:3}}}',
                 ['HOOL', 'USD']),
                ('10 {:4} {{100.00 {:3}}} @ 120.00 {:3}',
                 ['HOOL', 'USD', 'USD']),
        ]:
            for string in _gen_missing_combinations(template.format(pos_template), args):
                entries, errors, _ = parser.parse_string(string)
                self.assertFalse(errors)

    def test_all_interpolation_combinations(self):
        template = textwrap.dedent("""
          2015-10-02 *
            Assets:Account  {}
            Assets:Other
        """)
        for pos_template, args in [
                ('{:7} {:3}',
                 ['100.00', 'USD']),
                ('{:7} {:3} @ {:7} {:3}',
                 ['100.00', 'USD', '1.20', 'CAD']),
                ('{:2} {:4} {{{:7} {:3}}}',
                 ['10', 'HOOL', '100.00', 'USD']),
                ('{:2} {:4} {{{:7} # {:7} USD}}',
                 ['10', 'HOOL', '100.00', '9.95']),
                ('{:2} {:4} {{{:7} # {:7} USD}} @ {:7} {:3}',
                 ['10', 'HOOL', '100.00', '9.95', '120.00', 'USD']),
        ]:
            for string in _gen_missing_combinations(template.format(pos_template), args):
                entries, errors, _ = parser.parse_string(string)
                for error in errors:
                    oss = io.StringIO()
                    printer.print_error(error, oss)
                    oss.write("In transaction:\n")
                    oss.write(string)
                    self.fail(oss.getvalue())


def indexes(groups):
    """Return only the index sets from currency categorized groups."""
    return {currency: {refer[0] for refer in refers}
            for currency, refers in groups.items()}


class TestCategorizeCurrencyGroup(unittest.TestCase):
    "Tests of per-currency categorization of postings."

    @parser.parse_doc(allow_incomplete=True)
    def test_categorize__units__unambiguous(self, entries, _, options_map):
        """
        2015-10-02 *
          Assets:Account  100.00 USD
          Assets:Other   -100.00 USD

        2015-10-02 *
          Assets:Account         USD
          Assets:Other   -100.00 USD
        """
        for entry in entries:
            groups, errors = bf.categorize_by_currency(entry, {})
            self.assertFalse(errors)
            self.assertEqual({'USD': {0, 1}}, indexes(groups))

    @parser.parse_doc(allow_incomplete=True)
    def test_categorize__units__ambiguous(self, entries, _, options_map):
        """
        ;; Uses the other legs to disambiguate.
        2015-10-02 *
          Assets:Account  100.00
          Assets:Other   -100.00 USD

        ;; Uses the inventory contents to disambiguate.
        2015-10-02 *
          Assets:Account  100.00
          Assets:Other
        """
        groups, errors = bf.categorize_by_currency(entries[0], {})
        self.assertFalse(errors)
        self.assertEqual({'USD': {0, 1}}, indexes(groups))

        groups, errors = bf.categorize_by_currency(
            entries[1], {'Assets:Account': I('1.00 USD')})
        self.assertFalse(errors)
        self.assertEqual({'USD': {0, 1}}, indexes(groups))
        groups, errors = bf.categorize_by_currency(
            entries[1], {})
        self.assertTrue(errors)
        self.assertRegex(errors[0].message, 'Failed to categorize posting')
        self.assertEqual({}, indexes(groups))

    @parser.parse_doc(allow_incomplete=True)
    def test_categorize__units_price__unambiguous(self, entries, _, options_map):
        """
        2015-10-02 *
          Assets:Account  100.00 USD @ 1.20 CAD
          Assets:Other   -120.00 CAD

        2015-10-02 *
          Assets:Account  100.00     @ 1.20 CAD
          Assets:Other   -120.00 CAD
        """
        groups, errors = bf.categorize_by_currency(entries[0], {})
        self.assertFalse(errors)
        self.assertEqual({'CAD': {0, 1}}, indexes(groups))

        groups, errors = bf.categorize_by_currency(
            entries[1], {'Assets:Account': I('1.00 USD')})
        self.assertFalse(errors)
        self.assertEqual({'CAD': {0, 1}}, indexes(groups))
        groups, errors = bf.categorize_by_currency(
            entries[1], {})
        self.assertTrue(errors)
        self.assertRegex(errors[0].message, 'Could not resolve units currency')
        self.assertEqual({'CAD': {0, 1}}, indexes(groups))

    @parser.parse_doc(allow_incomplete=True)
    def test_categorize__units_price__ambiguous(self, entries, _, options_map):
        """
        ;; Uses the other legs to disambiguate.
        2015-10-02 *
          Assets:Account  100.00 USD @ 1.20
          Assets:Other   -120.00 CAD

        2015-10-02 *
          Assets:Account  100.00     @ 1.20
          Assets:Other   -120.00 CAD

        ;; These cases fail, because using the inventory tells nothing which price to
        ;; convert from.
        2015-10-02 *
          Assets:Account  100.00 USD @ 1.20
          Assets:Other

        2015-10-02 *
          Assets:Account  100.00     @ 1.20
          Assets:Other
        """
        groups, errors = bf.categorize_by_currency(entries[0], {})
        self.assertFalse(errors)
        self.assertEqual({'CAD': {0, 1}}, indexes(groups))

        groups, errors = bf.categorize_by_currency(
            entries[1], {'Assets:Account': I('1.00 USD')})
        self.assertFalse(errors)
        self.assertEqual({'CAD': {0, 1}}, indexes(groups))
        groups, errors = bf.categorize_by_currency(entries[1], {})
        self.assertTrue(errors)
        self.assertRegex(errors[0].message, 'Could not resolve units currency')
        self.assertEqual({'CAD': {0, 1}}, indexes(groups))

        for i in 2, 3:
            groups, errors = bf.categorize_by_currency(entries[i], {})
            self.assertEqual(1, len(errors))
            self.assertRegex(errors[0].message, 'Failed to categorize posting')
            self.assertEqual({}, indexes(groups))

    @parser.parse_doc(allow_incomplete=True)
    def test_categorize__units_cost__unambiguous(self, entries, _, options_map):
        """
        2015-10-02 *
          Assets:Account    10 HOOL {100.00 USD}
          Assets:Other   -1000 USD

        2015-10-02 *
          Assets:Account    10      {100.00 USD}
          Assets:Other   -1000 USD
        """
        groups, errors = bf.categorize_by_currency(entries[0], {})
        self.assertFalse(errors)
        self.assertEqual({'USD': {0, 1}}, indexes(groups))

        groups, errors = bf.categorize_by_currency(
            entries[1], {'Assets:Account': I('1 HOOL {1.00 USD}')})
        self.assertFalse(errors)
        self.assertEqual({'USD': {0, 1}}, indexes(groups))
        groups, errors = bf.categorize_by_currency(entries[1], {})
        self.assertTrue(errors)
        self.assertRegex(errors[0].message, 'Could not resolve units currency')
        self.assertEqual({'USD': {0, 1}}, indexes(groups))

    @parser.parse_doc(allow_incomplete=True)
    def test_categorize__units_cost__ambiguous(self, entries, _, options_map):
        """
        ;; Uses the other legs to disambiguate.
        2015-10-02 *
          Assets:Account    10 HOOL {100.00    }
          Assets:Other   -1000 USD

        2015-10-02 *
          Assets:Account    10      {100.00    }
          Assets:Other   -1000 USD

        ;; Disambiguate using the inventory cost, if some other lots exist in the
        ;; balance.
        2015-10-02 *
          Assets:Account    10 HOOL {100.00    }
          Assets:Other

        2015-10-02 *
          Assets:Account    10      {100.00    }
          Assets:Other
        """
        groups, errors = bf.categorize_by_currency(entries[0], {})
        self.assertFalse(errors)
        self.assertEqual({'USD': {0, 1}}, indexes(groups))

        groups, errors = bf.categorize_by_currency(
            entries[1], {'Assets:Account': I('1 HOOL {1.00 USD}')})
        self.assertFalse(errors)
        self.assertEqual({'USD': {0, 1}}, indexes(groups))
        groups, errors = bf.categorize_by_currency(entries[1], {})
        self.assertTrue(errors)
        self.assertRegex(errors[0].message, 'Could not resolve units currency')
        self.assertEqual({'USD': {0, 1}}, indexes(groups))

        for i in 2, 3:
            groups, errors = bf.categorize_by_currency(
                entries[i], {'Assets:Account': I('1 HOOL {1.00 USD}')})
            self.assertFalse(errors)
            self.assertEqual({'USD': {0, 1}}, indexes(groups))
            groups, errors = bf.categorize_by_currency(
                entries[i], {})
            self.assertEqual(1, len(errors))
            self.assertRegex(errors[0].message, 'Failed to categorize posting')
            self.assertEqual({}, indexes(groups))

    @parser.parse_doc(allow_incomplete=True)
    def test_categorize__units_cost_price__unambiguous(self, entries, _, options_map):
        """
        2015-10-02 *
          Assets:Account  10 HOOL {100.00 USD} @ 120.00 USD
          Assets:Other

        2015-10-02 *
          Assets:Account  10      {100.00 USD} @ 120.00 USD
          Assets:Other

        2015-10-02 *
          Assets:Account  10 HOOL {100.00    } @ 120.00 USD
          Assets:Other

        2015-10-02 *
          Assets:Account  10      {100.00    } @ 120.00 USD
          Assets:Other

        2015-10-02 *
          Assets:Account  10 HOOL {100.00 USD} @ 120.00
          Assets:Other

        2015-10-02 *
          Assets:Account  10      {100.00 USD} @ 120.00
          Assets:Other
        """
        for i in 0, 2, 4:
            groups, errors = bf.categorize_by_currency(entries[i], {})
            self.assertFalse(errors)
            self.assertEqual({'USD': {0, 1}}, indexes(groups))

        for i in 1, 3, 5:
            groups, errors = bf.categorize_by_currency(entries[i], {})
            self.assertEqual(1, len(errors))
            self.assertRegex(errors[0].message, 'Could not resolve units currency')
            self.assertEqual({'USD': {0, 1}}, indexes(groups))

    @parser.parse_doc(allow_incomplete=True)
    def test_categorize__units_cost_price__ambiguous(self, entries, _, options_map):
        """
        ;; Uses the other legs to disambiguate.
        2015-10-02 *
          Assets:Account   10 HOOL {100.00    } @ 120.00
          Assets:Other  -1000 USD

        2015-10-02 *
          Assets:Account   10      {100.00    } @ 120.00
          Assets:Other  -1000 USD

        ;; Uses the cost to disambiguate.
        2015-10-02 *
          Assets:Account   10 HOOL {100.00    } @ 120.00
          Assets:Other

        2015-10-02 *
          Assets:Account   10      {100.00    } @ 120.00
          Assets:Other
        """
        groups, errors = bf.categorize_by_currency(entries[0], {})
        self.assertFalse(errors)
        self.assertEqual({'USD': {0, 1}}, indexes(groups))

        groups, errors = bf.categorize_by_currency(entries[1], {})
        self.assertTrue(errors)
        self.assertRegex(errors[0].message, 'Could not resolve units currency')
        self.assertEqual({'USD': {0, 1}}, indexes(groups))

        for i in 2, 3:
            groups, errors = bf.categorize_by_currency(
                entries[i], {'Assets:Account': I('1 HOOL {1.00 USD}')})
            self.assertFalse(errors)
            self.assertEqual({'USD': {0, 1}}, indexes(groups))
            groups, errors = bf.categorize_by_currency(
                entries[i], {})
            self.assertTrue(errors)
            self.assertRegex(errors[0].message, 'Failed to categorize posting')
            self.assertEqual({}, indexes(groups))

    @parser.parse_doc(allow_incomplete=True)
    def test_categorize__multiple_auto_postings(self, entries, _, options_map):
        """
        2015-10-02 *
          Assets:Account   100.00 USD
          Assets:Account   100.00 CAD
          Assets:Other
        """
        groups, errors = bf.categorize_by_currency(entries[0], {})
        self.assertFalse(errors)
        self.assertEqual({'USD': {0, 2}, 'CAD': {1, 2}}, indexes(groups))

    @parser.parse_doc(allow_incomplete=True)
    def test_categorize__redundant_auto_postings(self, entries, _, options_map):
        """
        ;; Uses the other legs to disambiguate.
        2015-10-02 *
          Assets:Account   10 HOOL {100.00    } @ 120.00
          Assets:Other
          Assets:Other
        """
        groups, errors = bf.categorize_by_currency(entries[0], {})
        self.assertTrue(errors)


class TestReplaceCurrenciesInGroup(unittest.TestCase):
    "Tests the replacement of currencies inferred in the categorization step."

    def check(self, expected, entry):
        groups, errors = bf.categorize_by_currency(entry, {})
        self.assertFalse(errors)
        posting_groups = bf.replace_currencies(entry.postings, groups)
        check_groups = {
            currency: [(posting.account,
                        posting.units.currency,
                        posting.cost.currency if posting.cost else None,
                        posting.price.currency if posting.price else None)
                       for posting in postings]
            for currency, postings in posting_groups.items()}
        self.assertEqual(expected, check_groups)

        # Check all the postings are unique instances.
        all_postings = [posting
                        for postings in posting_groups.values()
                        for posting in postings]
        self.assertEqual(len(set(map(id, all_postings))), len(all_postings))

    @parser.parse_doc(allow_incomplete=True)
    def test_auto_posting(self, entries, _, options_map):
        """
        2015-10-02 *
          Assets:Account   100.00 USD
          Assets:Other

        2015-10-02 *
          Assets:Account   100.00 USD
          Assets:Account   100.00 CAD
          Assets:Other

        2015-10-02 *
          Assets:Account   100.00 USD
          Assets:Account   100.00 CAD
          Assets:US:Other  USD
          Assets:CA:Other  CAD
        """
        self.check({'USD': [('Assets:Account', 'USD', None, None),
                            ('Assets:Other', 'USD', None, None)]}, entries[0])
        self.check({'CAD': [('Assets:Account', 'CAD', None, None),
                            ('Assets:Other', 'CAD', None, None)],
                    'USD': [('Assets:Account', 'USD', None, None),
                            ('Assets:Other', 'USD', None, None)]}, entries[1])
        self.check({'CAD': [('Assets:Account', 'CAD', None, None),
                            ('Assets:CA:Other', 'CAD', None, None)],
                    'USD': [('Assets:Account', 'USD', None, None),
                            ('Assets:US:Other', 'USD', None, None)]}, entries[2])

    @parser.parse_doc(allow_incomplete=True)
    def test_missing(self, entries, _, options_map):
        """
        2015-10-02 *
          Assets:Account   100.00
          Assets:Another  -100.00 USD

        2015-10-02 *
          Assets:Account   100.00 USD @ 120.00
          Assets:Another  -120.00 CAD

        2015-10-02 *
          Assets:Account   10 HOOL {100.00}
          Assets:Another  -1000.00 USD

        2015-10-02 *
          Assets:Account   10 HOOL {100.00} @ 120.00 USD
          Assets:Another  -1000.00 USD
        2015-10-02 *
          Assets:Account   10 HOOL {100.00 USD} @ 120.00
          Assets:Another  -1000.00 USD
        """
        self.check({'USD': [('Assets:Account', 'USD', None, None),
                            ('Assets:Another', 'USD', None, None)]}, entries[0])

        self.check({'CAD': [('Assets:Account', 'USD', None, 'CAD'),
                            ('Assets:Another', 'CAD', None, None)]}, entries[1])

        self.check({'USD': [('Assets:Account', 'HOOL', 'USD', None),
                            ('Assets:Another', 'USD', None, None)]}, entries[2])

        self.check({'USD': [('Assets:Account', 'HOOL', 'USD', 'USD'),
                            ('Assets:Another', 'USD', None, None)]}, entries[3])
        self.check({'USD': [('Assets:Account', 'HOOL', 'USD', 'USD'),
                            ('Assets:Another', 'USD', None, None)]}, entries[4])


def normalize_postings(postings):
    """Normalize a list of postings ready for direct comparison, for testing.

    This sorts them by line order and removes metadata.

    Args:
      postings: A list of Posting instances.
    Returns:
      A new reordered and normalized Posting instances.
    """
    return [posting._replace(meta=None)
            for posting in sorted(postings,
                                  key=lambda posting: posting.meta['lineno'])]


class TestInterpolateCurrencyGroup(unittest.TestCase):
    "Tests the replacement of currencies inferred in the categorization step."

    maxDiff = 8192

    # 'expected' is a mapping of currency to tuples of
    #   interpolated: A boolean, asserting the return value of interpolate_group().
    #   string: A string, to be parsed to obtain the resulting Posting instances.
    #   errors: A list of error strings to check against the interpolation for that group.
    def check(self, entry, expected, balances=None, debug=False):
        if balances is None:
            balances = {}

        groups, errors = bf.categorize_by_currency(entry, balances)
        self.assertFalse(errors)
        posting_groups = bf.replace_currencies(entry.postings, groups)
        for currency, postings in posting_groups.items():
            try:
                exp_interpolated, exp_string, exp_errors = expected[currency]
            except KeyError:
                self.fail("Currency {} is unexpected".format(currency))

            # Run the interpolation for that group.
            new_postings, errors, interpolated = bf.interpolate_group(
                postings, balances, currency)

            # Print out infos for troubleshooting.
            if debug:
                print()
                for posting in new_postings:
                    print(posting)
                for error in errors:
                    print(error)
                print()

            # Check the expectation on interpolation.
            self.assertEqual(exp_interpolated, interpolated)

            # Check the expected number of errors.
            self.assertEqual(len(exp_errors) if exp_errors else 0, len(errors))
            if exp_errors:
                for exp_error in exp_errors:
                    self.assertTrue(any(re.match(exp_error, error.message)
                                        for error in errors))

            # Check the expected postings.
            if exp_string is not None:
                exp_entries, err1, _ = parser.parse_string(exp_string, dedent=True)
                exp_entries, err2 = booking_simple.convert_lot_specs_to_lots(exp_entries)
                self.assertFalse(err1 or err2, "Internal error in test")
                self.assertEqual(1, len(exp_entries),
                                 "Internal error, expected one entry")
                exp_postings = normalize_postings(exp_entries[0].postings)
                self.assertEqual(exp_postings, normalize_postings(new_postings))

        return errors

    @parser.parse_doc(allow_incomplete=True)
    def test_complete(self, entries, _, options_map):
        """
        2015-10-02 *
          Assets:Account   100.00 USD
          Assets:Other    -100.00 USD
        """
        self.check(entries[0], {'USD': (False, None, None)})

    @parser.parse_doc(allow_incomplete=True)
    def test_incomplete_impossible_twomiss_diff_units(self, entries, _, options_map):
        """
        2015-10-02 *
          Assets:Account          USD
          Assets:Other            USD
        """
        self.check(entries[0], {
            'USD': (False, None, ["Too many missing numbers for currency group"])})

    @parser.parse_doc(allow_incomplete=True)
    def test_incomplete_impossible_twomiss_diff_cost_and_units(self,
                                                               entries, _, options_map):
        """
        2015-10-02 *
          Assets:Account   2 HOOL {USD}
          Assets:Other       USD
        """
        self.check(entries[0], {
            'USD': (False, None, ["Too many missing numbers for currency group"])})

    @parser.parse_doc(allow_incomplete=True)
    def test_incomplete_impossible_miss_same_posting(self, entries, _, options_map):
        """
        2015-10-02 *
          Assets:Account            HOOL {USD}
          Assets:Other      -100.00 USD
        """
        self.check(entries[0], {
            'USD': (False, None, ["Too many missing numbers for currency group"])})

    @parser.parse_doc(allow_incomplete=True)
    def test_incomplete_units(self, entries, _, options_map):
        """
        2015-10-02 *
          Assets:Account          USD
          Assets:Other    -100.00 USD

        2015-10-02 *
          Assets:Account          HOOL {100.00 # 9.95 USD}
          Assets:Other   -1009.95 USD

        2015-10-02 *
          Assets:Account          HOOL {100.00 USD}
          Assets:Other   -1000.00 USD

        2015-10-02 *
          Assets:Account          HOOL {100.00 USD} @ 110.00 USD
          Assets:Other   -1000.00 USD

        2015-10-02 *
          Assets:Account          HOOL {0 # 1009.95 USD}
          Assets:Other   -1009.95 USD

        2015-10-02 *
          Assets:Account          CAD @ 1.25 USD
          Assets:Other    -100.00 USD
        """
        self.check(entries[0], {
            'USD': (True, """
              2015-10-02 *
                Assets:Account   100.00 USD
                Assets:Other    -100.00 USD
            """, None)})

        self.check(entries[1], {
            'USD': (True, """
              2015-10-02 *
                Assets:Account       10 HOOL {100.00 # 9.95 USD}
                Assets:Other   -1009.95 USD
            """, None)})

        self.check(entries[2], {
            'USD': (True, """
              2015-10-02 *
                Assets:Account       10 HOOL {100.00 USD}
                Assets:Other   -1000.00 USD
            """, None)})

        self.check(entries[3], {
            'USD': (True, """
              2015-10-02 *
                Assets:Account       10 HOOL {100.00 USD} @ 110.00 USD
                Assets:Other   -1000.00 USD
            """, None)})

        # Check impossible case.
        self.check(entries[4], {
            'USD': (True, None, ["Cannot infer per-unit cost only from total"])})

        self.check(entries[5], {
            'USD': (True, """
              2015-10-02 *
                Assets:Account    80.00 CAD @ 1.25 USD
                Assets:Other    -100.00 USD
            """, None)})

    @parser.parse_doc(allow_incomplete=True)
    def test_incomplete_cost_both(self, entries, _, options_map):
        """
        2015-10-02 *
          Assets:Account       10 HOOL {USD}
          Assets:Other   -1009.95 USD

        2015-10-02 *
          Assets:Account       10 HOOL {USD} @ 110.00 USD
          Assets:Other   -1009.95 USD

        2015-10-02 *
          Assets:Account       10 HOOL {USD, "blah"}
          Assets:Other   -1009.95 USD
        """
        self.check(entries[0], {
            'USD': (True, """
              2015-10-02 *
                Assets:Account       10 HOOL {100.995 USD}
                Assets:Other   -1009.95 USD
            """, None)})
        self.check(entries[1], {
            'USD': (True, """
              2015-10-02 *
                Assets:Account       10 HOOL {100.995 USD} @ 110.00 USD
                Assets:Other   -1009.95 USD
            """, None)})
        self.check(entries[2], {
            'USD': (True, """
              2015-10-02 *
                Assets:Account       10 HOOL {100.995 USD, "blah"}
                Assets:Other   -1009.95 USD
            """, None)})

    @parser.parse_doc(allow_incomplete=True)
    def test_incomplete_cost_per(self, entries, _, options_map):
        """
        2015-10-02 *
          Assets:Account       10 HOOL {# 9.95 USD}
          Assets:Other   -1009.95 USD

        2015-10-02 *
          Assets:Account       10 HOOL {# 9.95 USD} @ 110.00 USD
          Assets:Other   -1009.95 USD
        """
        self.check(entries[0], {
            'USD': (True, """
              2015-10-02 *
                Assets:Account       10 HOOL {100.00 # 9.95 USD}
                Assets:Other   -1009.95 USD
            """, None)})
        self.check(entries[1], {
            'USD': (True, """
              2015-10-02 *
                Assets:Account       10 HOOL {100.00 # 9.95 USD} @ 110.00 USD
                Assets:Other   -1009.95 USD
            """, None)})

    @parser.parse_doc(allow_incomplete=True)
    def test_incomplete_cost_total(self, entries, _, options_map):
        """
        2015-10-02 *
          Assets:Account       10 HOOL {100.00 # USD}
          Assets:Other   -1009.95 USD

        2015-10-02 *
          Assets:Account       10 HOOL {100.00 # USD} @ 110.00 USD
          Assets:Other   -1009.95 USD
        """
        self.check(entries[0], {
            'USD': (True, """
              2015-10-02 *
                Assets:Account       10 HOOL {100.00 # 9.95 USD}
                Assets:Other   -1009.95 USD
            """, None)})
        self.check(entries[1], {
            'USD': (True, """
              2015-10-02 *
                Assets:Account       10 HOOL {100.00 # 9.95 USD} @ 110.00 USD
                Assets:Other   -1009.95 USD
            """, None)})

    @parser.parse_doc(allow_incomplete=True)
    def test_incomplete_price(self, entries, _, options_map):
        """
        2015-10-02 *
          Assets:Account  120.00 CAD @ USD
          Assets:Other   -100.00 USD

        2015-10-02 *
          Assets:Account       10 HOOL {100.00 # 9.95 USD} @ USD
          Assets:Other   -1009.95 USD
        """
        self.check(entries[0], {
            'USD': (True, """
              2015-10-02 *
                Assets:Account  120.00 CAD @ 1.2 USD
                Assets:Other   -100.00 USD
            """, None)})
        self.check(entries[1], {
            'USD': (True, None,
                    ["Cannot infer price for postings with units held at cost"])})

    @parser.parse_doc(allow_incomplete=True)
    def test_multiple_groups(self, entries, _, options_map):
        """
          2010-05-28 *
            Assets:Account1     100.00 CAD
            Assets:Account2     -80.00 CAD
            Assets:Account3            CAD
            Assets:Account4     200.00 USD
            Assets:Account5            USD

          2010-05-28 *
            Assets:Account1     100.00 CAD
            Assets:Account2     -80.00 CAD
            Assets:Account3     -20.00 CAD
            Assets:Account4     200.00 USD
            Assets:Account5            USD
        """
        for entry in entries:
            self.check(entries[0], {
                'CAD': (True, """
                  2010-05-28 *
                    Assets:Account1     100.00 CAD
                    Assets:Account2     -80.00 CAD
                    Assets:Account3     -20.00 CAD
                """, None),
                'USD': (True, """
                  2010-05-28 *
                    Assets:Account4     200.00 USD
                    Assets:Account5    -200.00 USD
                """, None)})

    @parser.parse_doc(allow_incomplete=True)
    def test_incomplete_underdefined(self, entries, _, options_map):
        """
        2015-10-02 *
          Assets:Account        -10 HOOL {USD} @ 120.00 USD
          Assets:Other      1000.00 USD
          Income:PnL
        """
        # Interpolation and booking both required... impossible.
        self.check(entries[0], {
            'USD': (False, None, ["Too many missing numbers for currency group"])
        })

    @parser.parse_doc(allow_incomplete=True)
    def test_incomplete_underdefined2(self, entries, _, options_map):
        """
        1997-03-16 * "Transfer"
          Assets:CA:Life:RRSP:Cash          2000 CAD
          Assets:CA:Pop:Checking
          Assets:CA:CRA:PreTaxRSP:Allowed  -2000 RSPCAD
          Assets:CA:CRA:PreTaxRSP:Unused    2000 RSPCAD
        """
        # Interpolation and booking both required... impossible.
        self.check(entries[0], {
            'CAD': (True, """
              1997-03-16 *
                Assets:CA:Life:RRSP:Cash    2000 CAD
                Assets:CA:Pop:Checking     -2000 CAD
            """, None),
            'RSPCAD': (False, """
              1997-03-16 *
                Assets:CA:CRA:PreTaxRSP:Allowed  -2000 RSPCAD
                Assets:CA:CRA:PreTaxRSP:Unused    2000 RSPCAD
            """, None)})

    @parser.parse_doc(allow_incomplete=True)
    def test_auto_posting__superfluous_unused(self, entries, errors, _):
        """
          2000-01-01 open Assets:Account1
          2000-01-01 open Assets:Account2

          2016-04-23 * ""
            Assets:Account1     0.00 USD
            Assets:Account2
        """
        self.check(entries[-1], {
            'USD': (False, """
              2016-04-23 * ""
                Assets:Account1     0.00 USD
            """, None)})
        # FIXME: This ought to return a "Superfluous posting" error for Account2 only.

    @parser.parse_doc(allow_incomplete=True)
    def test_auto_posting__superfluous_unneeded(self, entries, errors, _):
        """
          2000-01-01 open Assets:Account1
          2000-01-01 open Assets:Account2
          2000-01-01 open Assets:Account3

          2016-04-23 * ""
            Assets:Account1   100.00 USD
            Assets:Account2  -100.00 USD
            Assets:Account3
        """
        self.check(entries[-1], {
            'USD': (False, """
              2016-04-23 * ""
                Assets:Account1   100.00 USD
                Assets:Account2  -100.00 USD
            """, None)})
        # FIXME: This ought to return a "Superfluous posting" error for Account3 only.

    @parser.parse_doc(allow_incomplete=True)
    def test_auto_posting__superfluous_needed_one_side(self, entries, errors, _):
        """
          2000-01-01 open Assets:Account1
          2000-01-01 open Assets:Account2
          2000-01-01 open Assets:Account3
          2000-01-01 open Assets:Account4
          2000-01-01 open Assets:Account5

          2016-04-23 * ""
            Assets:Account1   100.00 USD
            Assets:Account2  -100.00 USD
            Assets:Account3   100.00 CAD
            Assets:Account4   -99.00 CAD
            Assets:Account5
        """
        self.check(entries[-1], {
            'USD': (False, """
              2016-04-23 * ""
                Assets:Account1   100.00 USD
                Assets:Account2  -100.00 USD
            """, None),
            'CAD': (True, """
              2016-04-23 * ""
                Assets:Account3   100.00 CAD
                Assets:Account4   -99.00 CAD
                Assets:Account5    -1.00 CAD
            """, None)})


class TestComputeCostNumber(unittest.TestCase):

    date = datetime.date(2016, 1, 1)

    def test_missing_per(self):
        self.assertEqual(
            None,
            bf.compute_cost_number(
                position.CostSpec(MISSING, D('1'), 'USD', None, None, False),
                amount.from_string('12 HOOL')))

    def test_missing_total(self):
        self.assertEqual(
            None,
            bf.compute_cost_number(
                position.CostSpec(D('1'), MISSING, 'USD', None, None, False),
                amount.from_string('12 HOOL')))

    def test_both_none(self):
        self.assertEqual(
            None,
            bf.compute_cost_number(
                position.CostSpec(None, None, 'USD', None, None, False),
                amount.from_string('12 HOOL')))

    def test_total_only(self):
        self.assertEqual(
            D('4'),
            bf.compute_cost_number(
                position.CostSpec(None, D('48'), 'USD', None, None, False),
                amount.from_string('12 HOOL')))

    def test_per_only(self):
        self.assertEqual(
            D('4'),
            bf.compute_cost_number(
                position.CostSpec(D('4'), None, 'USD', None, None, False),
                amount.from_string('12 HOOL')))

    def test_both(self):
        self.assertEqual(
            D('3.5'),
            bf.compute_cost_number(
                position.CostSpec(D('3'), D('6'), 'USD', self.date, None, False),
                amount.from_string('12 HOOL')))

    def test_no_currency(self):
        self.assertEqual(
            D('3.5'),
            bf.compute_cost_number(
                position.CostSpec(D('3'), D('6'), None, self.date, None, False),
                amount.from_string('12 HOOL')))


class TestParseBookingOptions(cmptest.TestCase):

    @loader.load_doc()
    def test_booking_algorithm__simple(self, entries, _, options_map):
        """
          option "experiment_booking_algorithm" "SIMPLE"
        """
        self.assertEqual("SIMPLE", options_map["experiment_booking_algorithm"])

    @loader.load_doc()
    def test_booking_algorithm__full(self, entries, _, options_map):
        """
          option "experiment_booking_algorithm" "FULL"
        """
        self.assertEqual("FULL", options_map["experiment_booking_algorithm"])

    @loader.load_doc(expect_errors=True)
    def test_booking_algorithm__invalid(self, entries, errors, options_map):
        """
          option "experiment_booking_algorithm" "XXX"
        """
        self.assertEqual(1, len(errors))

    @loader.load_doc()
    def test_booking_method__strict(self, entries, _, options_map):
        """
          option "booking_method" "STRICT"
        """
        self.assertEqual(Booking.STRICT, options_map["booking_method"])

    @loader.load_doc()
    def test_booking_method__average(self, entries, _, options_map):
        """
          option "booking_method" "AVERAGE"
        """
        self.assertEqual(Booking.AVERAGE, options_map["booking_method"])

    @loader.load_doc(expect_errors=True)
    def test_booking_method__invalid(self, _, errors, options_map):
        """
          option "booking_method" "XXX"
        """
        self.assertEqual(1, len(errors))
        self.assertEqual(Booking.STRICT,
                         options_map["booking_method"])


class TestBookReductions(unittest.TestCase):
    """Tests the booking of inventory reductions.
    Note that this is expected to leave reductions unmodified.
    """

    maxDiff = 8192

    BM = collections.defaultdict(lambda: Booking.STRICT)

    #
    # Test that the augmentations are left alone by the book_reductions() function.
    #

    @parser.parse_doc(allow_incomplete=True)
    def test_augment__from_empty__no_cost(self, entries, _, __):
        """
        2015-10-01 * "Regular currency, positive"
          Assets:Account1          1 USD
          Assets:Other

        2015-10-01 * "Regular currency, negative"
          Assets:Account2         -1 USD
          Assets:Other
        """
        for entry in entries:
            postings, errors = bf.book_reductions(entry, entry.postings, {}, self.BM)
            self.assertFalse(errors)
            self.assertEqual(len(postings), len(entry.postings))
            self.assertEqual(None, postings[0].cost)

    @parser.parse_doc(allow_incomplete=True)
    def test_augment__from_empty__at_cost(self, entries, _, __):
        """
        2015-10-01 * "At cost, positive"
          Assets:Account3          1 HOOL {100.00 USD}
          Assets:Other

        2015-10-01 * "At cost, negative"
          Assets:Account4         -1 HOOL {100.00 USD}
          Assets:Other
        """
        for entry in entries:
            postings, errors = bf.book_reductions(entry, entry.postings, {}, self.BM)
            self.assertFalse(errors)
            self.assertEqual(len(postings), len(entry.postings))
            self.assertEqual(
                position.CostSpec(D('100.00'), None, 'USD',
                                  datetime.date(2015, 10, 1), None, False),
                postings[0].cost)

    @parser.parse_doc(allow_incomplete=True)
    def test_augment__from_empty__incomplete_cost__empty(self, entries, _, __):
        """
        2015-10-01 * "At cost, no currency"
          Assets:Account3          1 HOOL {}
          Assets:Other
        """
        postings, errors = bf.book_reductions(entries[0], entries[0].postings, {}, self.BM)
        self.assertFalse(errors)
        self.assertEqual(
            CostSpec(MISSING, None, MISSING, datetime.date(2015, 10, 1), None, False),
            postings[0].cost)

    @parser.parse_doc(allow_incomplete=True)
    def test_augment__from_empty__incomplete_cost__with_currency(self, entries, _, __):
        """
        2015-10-01 * "At cost, incomplete"
          Assets:Account3          1 HOOL {USD}
          Assets:Other
        """
        postings, errors = bf.book_reductions(entries[0], entries[0].postings, {}, self.BM)
        self.assertFalse(errors)
        self.assertEqual(
            CostSpec(MISSING, None, 'USD', datetime.date(2015, 10, 1), None, False),
            postings[0].cost)

    @parser.parse_doc(allow_incomplete=True)
    def test_augment__no_cost(self, entries, _, __):
        """
        2015-10-01 * "Regular currency, positive, from empty"
          Assets:Account1          1 USD
          Assets:Other

        2015-10-01 * "Regular currency, positive, not empty"
          Assets:Account1          2 USD
          Assets:Other
        """
        for entry in entries:
            postings, errors = bf.book_reductions(entry, entry.postings, {}, self.BM)
            self.assertFalse(errors)
            self.assertEqual(postings, entry.postings)

    #
    # Test that reductions with no cost basis are left alone as well.
    #

    @parser.parse_doc(allow_incomplete=True)
    def test_reduce__no_cost(self, entries, _, __):
        """
        2015-10-01 * "Regular currency, positive, not empty"
          Assets:Account1          -5 USD
          Assets:Other
        """
        balances = {'Assets:Account1': I('10 USD')}
        entry = entries[-1]
        postings, errors = bf.book_reductions(entry, entry.postings, balances, self.BM)
        # Check that the posting was left alone, nothing to be matched.
        self.assertEqual(postings, entry.postings)

    #
    # Test reductions which trigger matching.
    #

    @parser.parse_doc(allow_incomplete=True)
    def test_reduce__no_match(self, entries, _, __):
        """
        2016-05-02 *
          Assets:Account          -5 HOOL {123.00 USD}
          Assets:Other

        2016-05-02 *
          Assets:Account          -5 HOOL {123.45 CAD}
          Assets:Other

        2016-05-02 *
          Assets:Account          -5 HOOL {123.45 USD, 2016-04-16}
          Assets:Other

        2016-05-02 *
          Assets:Account          -5 HOOL {123.45 USD, "lot1"}
          Assets:Other
        """
        balances = {'Assets:Account': I('10 HOOL {123.45 USD, 2016-04-15}')}
        for entry in entries:
            postings, errors = bf.book_reductions(entry, entry.postings, balances, self.BM)
            self.assertTrue(errors)
            self.assertRegex(errors[0].message, "No position matches")
            self.assertEqual(0, len(postings))

    @parser.parse_doc(allow_incomplete=True)
    def test_reduce__unambiguous(self, entries, _, __):
        """
        2016-05-02 *
          Assets:Account          -5 HOOL {}
          Assets:Other
        """
        ante_inv = I('10 HOOL {115.00 USD, 2016-04-15, "lot1"}')
        balances = {'Assets:Account': ante_inv}
        entry = entries[0]
        postings, errors = bf.book_reductions(entry, entry.postings, balances, self.BM)
        self.assertFalse(errors)
        self.assertEqual(2, len(postings))
        self.assertEqual(ante_inv[0].cost, postings[0].cost)

    @parser.parse_doc(allow_incomplete=True)
    def test_reduce__ambiguous__strict(self, entries, _, __):
        """
        2016-05-02 *
          Assets:Account          -5 HOOL {}
          Assets:Other

        2016-05-02 *
          Assets:Account          -5 HOOL {115.00 USD}
          Assets:Other

        2016-05-02 *
          Assets:Account          -5 HOOL {USD}
          Assets:Other

        2016-05-02 *
          Assets:Account          -5 HOOL {2016-04-15}
          Assets:Other
        """
        balances = {'Assets:Account': I('10 HOOL {115.00 USD, 2016-04-15, "lot1"}, '
                                        '10 HOOL {115.00 USD, 2016-04-15, "lot2"}')}
        for entry in entries:
            postings, errors = bf.book_reductions(entry, entry.postings, balances, self.BM)
            self.assertTrue(errors)
            self.assertRegex(errors[0].message, "Ambiguous matches")
            self.assertEqual(0, len(postings))

    @parser.parse_doc(allow_incomplete=True)
    def test_reduce__ambiguous__none(self, entries, _, __):
        """
        ; option "booking_method" "NONE"

        2016-05-02 *
          Assets:Account          -5 HOOL {117.00 USD}
          Assets:Other
        """
        BM = collections.defaultdict(lambda: Booking.NONE)
        balances = {'Assets:Account': I('1 HOOL {115.00 USD}, '
                                        '2 HOOL {116.00 USD}')}
        entry = entries[0]
        postings, errors = bf.book_reductions(entry, entry.postings, balances, BM)
        self.assertFalse(errors)
        self.assertEqual(2, len(postings))

    @parser.parse_doc(allow_incomplete=True)
    def test_reduce__ambiguous__none__from_mixed(self, entries, _, options_map):
        """
        ; option "booking_method" "NONE"

        2016-05-02 *
          Assets:Account          -5 HOOL {117.00 USD}
          Assets:Other
        """
        BM = collections.defaultdict(lambda: Booking.NONE)
        balances = {'Assets:Account': I('1 HOOL {115.00 USD}, '
                                        '-2 HOOL {116.00 USD}')}
        entry = entries[0]
        postings, errors = bf.book_reductions(entry, entry.postings, balances, BM)
        self.assertFalse(errors)
        self.assertEqual(2, len(postings))

    @parser.parse_doc(allow_incomplete=True)
    def test_reduce__other_currency(self, entries, _, __):
        """
        2016-05-02 *
          Assets:Account          -5 HOOL {115.00 USD}
          Assets:Other
        """
        balances = {'Assets:Account': I('8 AAPL {115.00 USD, 2016-01-11}, '
                                        '8 HOOL {115.00 USD, 2016-01-10}')}
        entry = entries[0]
        postings, errors = bf.book_reductions(entry, entry.postings, balances, self.BM)
        self.assertFalse(errors)
        self.assertEqual(2, len(postings))
        self.assertEqual(Cost(D('115.00'), 'USD', datetime.date(2016, 1, 10), None),
                         postings[0].cost)

    @parser.parse_doc(allow_incomplete=True)
    def test_reduce__multiple_reductions(self, entries, _, __):
        """
        2016-05-02 *
          Assets:Account          -40 HOOL {}
          Assets:Account          -35 HOOL {}
        """
        balances = {'Assets:Account': I('50 HOOL {115.00 USD, 2016-01-15}, '
                                        '50 HOOL {116.00 USD, 2016-01-16}')}
        entry = entries[0]
        BM = collections.defaultdict(lambda: Booking.FIFO)
        postings, errors = bf.book_reductions(entry, entry.postings, balances, BM)
        self.assertFalse(errors)
        self.assertEqual([
            data.Posting('Assets:Account', A('-40 HOOL'), Cost(D('115.00'), 'USD', datetime.date(2016, 1, 15), None), None, None, None),
            data.Posting('Assets:Account', A('-10 HOOL'), Cost(D('115.00'), 'USD', datetime.date(2016, 1, 15), None), None, None, None),
            data.Posting('Assets:Account', A('-25 HOOL'), Cost(D('116.00'), 'USD', datetime.date(2016, 1, 16), None), None, None, None),
            ], [posting._replace(meta=None) for posting in postings])

    @parser.parse_doc(allow_incomplete=True)
    def test_reduce__multiple_reductions__with_error(self, entries, _, __):
        """
        2016-05-02 *
          Assets:Account          -40 HOOL {}
          Assets:Account          -65 HOOL {}
        """
        balances = {'Assets:Account': I('50 HOOL {115.00 USD, 2016-01-15}, '
                                        '50 HOOL {116.00 USD, 2016-01-16}')}
        entry = entries[0]
        BM = collections.defaultdict(lambda: Booking.FIFO)
        postings, errors = bf.book_reductions(entry, entry.postings, balances, BM)
        self.assertTrue(errors)
        self.assertRegex(errors[0].message, 'Not enough lots to reduce')
        self.assertEqual([], postings)










class TestHandleAmbiguousMatches(unittest.TestCase):

    maxDiff = 8192


    def check(self, balance_entry, reduction, booking_method):
        matches = [Position(posting.units,
                            booking_simple.convert_spec_to_cost(posting.units,
                                                                posting.cost))
                   for posting in balance_entry.postings]
        entry = data.Transaction(None, datetime.date.today(), "*", None, "", None, None, [])
        return bf.handle_ambiguous_matches(entry, reduction, matches, booking_method)

    @parser.parse_doc(allow_incomplete=True)
    def test_ambiguous__NONE(self, entries, _, __):
        """
        2015-01-01 * "Non-mixed"
          Assets:Account          5 HOOL {100.00 USD, 2015-10-01}
          Assets:Account          5 HOOL {101.00 USD, 2015-10-01}

        2015-01-01 * "Mixed"
          Assets:Account          5 HOOL {100.00 USD, 2015-10-01}
          Assets:Account         -5 HOOL {101.00 USD, 2015-10-01}

        2015-06-01 * "Test"
          Assets:Account         -2 HOOL {102.00 USD, 2015-06-01}
          Assets:Account          2 HOOL {102.00 USD, 2015-06-01}
        """
        for posting in entries[-1].postings:
            postings, errors = self.check(entries[0], posting, Booking.NONE)
            self.assertEqual(1, len(postings))
            self.assertEqual(posting, postings[0])
            self.assertFalse(errors)

        for posting in entries[-1].postings:
            postings, errors = self.check(entries[1], posting, Booking.NONE)
            self.assertEqual(1, len(postings))
            self.assertEqual(posting, postings[0])
            self.assertFalse(errors)

    @parser.parse_doc(allow_incomplete=True)
    def test_ambiguous__STRICT(self, entries, _, __):
        """
        2015-01-01 * "Non-mixed"
          Assets:Account          5 HOOL {100.00 USD, 2015-10-01}
          Assets:Account          5 HOOL {101.00 USD, 2015-10-01}

        2015-01-01 * "Mixed"
          Assets:Account          5 HOOL {100.00 USD, 2015-10-01}
          Assets:Account         -5 HOOL {101.00 USD, 2015-10-01}

        2015-06-01 * "Negative test"
          Assets:Account         -2 HOOL {102.00 USD, 2015-06-01}
          Assets:Account         -2 HOOL {102.00 USD}
          Assets:Account         -2 HOOL {2015-06-01}
          Assets:Account         -2 HOOL {100.00 USD, 2015-10-01}
        """
        for posting in entries[-1].postings:
            postings, errors = self.check(entries[0], posting, Booking.STRICT)
            self.assertTrue(errors)

    def _test_ambiguous(self, entries, booking_method):
        """Test the entries using the format examplified in the two following methods."""
        pre_entry = entries[0]
        for entry in entries[1:]:
            apply_posting = entry.postings[0]
            expected_postings = [
                posting._replace(cost=booking_simple.convert_spec_to_cost(posting.units,
                                                                          posting.cost))
                for posting in entry.postings[1:]]
            matched_postings, errors = self.check(pre_entry, apply_posting, booking_method)
            self.assertEqual(len(expected_postings), len(matched_postings))
            for expected_posting, matched_posting in zip(expected_postings,
                                                         matched_postings):
                self.assertEqual(expected_posting._replace(meta=None),
                                 matched_posting._replace(meta=None))
            expect_error = bool(entry.tags)
            self.assertEqual(expect_error, bool(errors))

    @parser.parse_doc(allow_incomplete=True)
    def test_ambiguous__FIFO(self, entries, _, __):
        """
        2015-01-01 * "Non-mixed"
          Assets:Account          5 HOOL {111.11 USD, 2015-10-02}
          Assets:Account          4 HOOL {100.00 USD, 2015-10-01}
          Assets:Account          6 HOOL {122.22 USD, 2015-10-03}

        ;; Each of the entries below is its on test. The first posting is applied.
        ;; The other ones represent the expected output

        2015-02-22 * "Test no match against any lots"
          Assets:Account          0 HOOL {}  ;; EXPECTED

        2015-02-22 * "Test match against partial first lot"
          Assets:Account         -2 HOOL {}  ;; EXPECTED
          Assets:Account         -2 HOOL {100.00 USD, 2015-10-01}

        2015-02-22 * "Test match against complete first lot"
          Assets:Account         -4 HOOL {}  ;; EXPECTED
          Assets:Account         -4 HOOL {100.00 USD, 2015-10-01}

        2015-02-22 * "Test partial match against first two lots"
          Assets:Account         -7 HOOL {}  ;; EXPECTED
          Assets:Account         -4 HOOL {100.00 USD, 2015-10-01}
          Assets:Account         -3 HOOL {111.11 USD, 2015-10-02}

        2015-02-22 * "Test complete match against first two lots"
          Assets:Account         -9 HOOL {}  ;; EXPECTED
          Assets:Account         -4 HOOL {100.00 USD, 2015-10-01}
          Assets:Account         -5 HOOL {111.11 USD, 2015-10-02}

        2015-02-22 * "Test partial match against first three lots"
          Assets:Account        -12 HOOL {}  ;; EXPECTED
          Assets:Account         -4 HOOL {100.00 USD, 2015-10-01}
          Assets:Account         -5 HOOL {111.11 USD, 2015-10-02}
          Assets:Account         -3 HOOL {122.22 USD, 2015-10-03}

        2015-02-22 * "Test complete match against first three lots"
          Assets:Account        -15 HOOL {}  ;; EXPECTED
          Assets:Account         -4 HOOL {100.00 USD, 2015-10-01}
          Assets:Account         -5 HOOL {111.11 USD, 2015-10-02}
          Assets:Account         -6 HOOL {122.22 USD, 2015-10-03}

        2015-02-22 * "Test matching more than is available" #error
          Assets:Account        -16 HOOL {}  ;; EXPECTED
          Assets:Account         -4 HOOL {100.00 USD, 2015-10-01}
          Assets:Account         -5 HOOL {111.11 USD, 2015-10-02}
          Assets:Account         -6 HOOL {122.22 USD, 2015-10-03}
        """
        self._test_ambiguous(entries, Booking.FIFO)

    @parser.parse_doc(allow_incomplete=True)
    def test_ambiguous__LIFO(self, entries, _, __):
        """
        2015-01-01 * "Non-mixed"
          Assets:Account          5 HOOL {111.11 USD, 2015-10-02}
          Assets:Account          4 HOOL {100.00 USD, 2015-10-01}
          Assets:Account          6 HOOL {122.22 USD, 2015-10-03}

        ;; Each of the entries below is its on test. The first posting is applied.
        ;; The other ones represent the expected output

        2015-02-22 * "Test no match against any lots"
          Assets:Account          0 HOOL {}  ;; EXPECTED

        2015-02-22 * "Test match against partial first lot"
          Assets:Account         -2 HOOL {}  ;; EXPECTED
          Assets:Account         -2 HOOL {122.22 USD, 2015-10-03}

        2015-02-22 * "Test match against complete first lot"
          Assets:Account         -6 HOOL {}  ;; EXPECTED
          Assets:Account         -6 HOOL {122.22 USD, 2015-10-03}

        2015-02-22 * "Test partial match against first two lots"
          Assets:Account         -7 HOOL {}  ;; EXPECTED
          Assets:Account         -6 HOOL {122.22 USD, 2015-10-03}
          Assets:Account         -1 HOOL {111.11 USD, 2015-10-02}

        2015-02-22 * "Test complete match against first two lots"
          Assets:Account        -11 HOOL {}  ;; EXPECTED
          Assets:Account         -6 HOOL {122.22 USD, 2015-10-03}
          Assets:Account         -5 HOOL {111.11 USD, 2015-10-02}

        2015-02-22 * "Test partial match against first three lots"
          Assets:Account        -12 HOOL {}  ;; EXPECTED
          Assets:Account         -6 HOOL {122.22 USD, 2015-10-03}
          Assets:Account         -5 HOOL {111.11 USD, 2015-10-02}
          Assets:Account         -1 HOOL {100.00 USD, 2015-10-01}

        2015-02-22 * "Test complete match against first three lots"
          Assets:Account        -15 HOOL {}  ;; EXPECTED
          Assets:Account         -6 HOOL {122.22 USD, 2015-10-03}
          Assets:Account         -5 HOOL {111.11 USD, 2015-10-02}
          Assets:Account         -4 HOOL {100.00 USD, 2015-10-01}

        2015-02-22 * "Test matching more than is available" #error
          Assets:Account        -16 HOOL {}  ;; EXPECTED
          Assets:Account         -6 HOOL {122.22 USD, 2015-10-03}
          Assets:Account         -5 HOOL {111.11 USD, 2015-10-02}
          Assets:Account         -4 HOOL {100.00 USD, 2015-10-01}
        """
        self._test_ambiguous(entries, Booking.LIFO)









# FIXME: Rewrite these tests.

class TestBook(unittest.TestCase):

    def book_reductions(self, entries, currency='USD'):
        balances = collections.defaultdict(inventory.Inventory)
        booking_methods = collections.defaultdict(lambda: Booking.STRICT)
        for entry in entries:
            (booked_postings,
             booked_errors) = bf.book_reductions(entry,
                                                           entry.postings,
                                                           balances,
                                                           booking_methods)
            (inter_postings,
             inter_errors,
             interpolated) = bf.interpolate_group(booked_postings,
                                                            balances,
                                                            currency)
            for posting in inter_postings:
                balances[posting.account].add_position(posting)

        return inter_postings, balances

    def assertPostingsEqual(self, postings1, postings2):
        postings1 = [posting._replace(meta=None) for posting in postings1]
        postings2 = [posting._replace(meta=None) for posting in postings2]
        self.assertEqual(postings1, postings2)

    @parser.parse_doc(allow_incomplete=True)
    def test_augment__at_cost__same(self, entries, _, __):
        """
        2015-10-01 * "Held-at-cost, positive"
          Assets:Account1          1 HOOL {100.00 USD}
          Assets:Other       -100.00 USD

        2015-10-01 * "Held-at-cost, positive, same cost"
          Assets:Account1          2 HOOL {100.00 USD}
          Assets:Other       -200.00 USD
        """
        postings, balances = self.book_reductions(entries)
        self.assertEqual(inventory.from_string('3 HOOL {100.00 USD, 2015-10-01}'),
                         balances['Assets:Account1'])
        self.assertPostingsEqual([
            data.Posting('Assets:Account1', A('2 HOOL'),
                         Cost(D('100.00'), 'USD', datetime.date(2015, 10, 1), None),
                         None, None, None),
            data.Posting('Assets:Other', A('-200.00 USD'), None, None, None, None),
            ], postings)

    @parser.parse_doc(allow_incomplete=True)
    def test_augment__at_cost__different_date(self, entries, _, __):
        """
        2015-10-01 * "Held-at-cost, positive"
          Assets:Account1          1 HOOL {100.00 USD}
          Assets:Other          -100.00 USD

        2015-10-02 * "Held-at-cost, positive, same cost"
          Assets:Account1          2 HOOL {100.00 USD}
          Assets:Other          -200.00 USD
        """
        postings, balances = self.book_reductions(entries)
        self.assertEqual(inventory.from_string('1 HOOL {100.00 USD, 2015-10-01}, '
                                               '2 HOOL {100.00 USD, 2015-10-02}'),
                         balances['Assets:Account1'])
        self.assertPostingsEqual([
            data.Posting('Assets:Account1', A('2 HOOL'),
                         Cost(D('100.00'), 'USD', datetime.date(2015, 10, 2), None),
                         None, None, None),
            data.Posting('Assets:Other', A('-200.00 USD'), None, None, None, None),
            ], postings)

    @parser.parse_doc(allow_incomplete=True)
    def test_augment__at_cost__different_date__overridden(self, entries, _, __):
        """
        2015-10-01 * "Held-at-cost, positive"
          Assets:Account1          1 HOOL {100.00 USD}
          Assets:Other          -100.00 USD

        2015-10-01 * "Held-at-cost, positive, same cost"
          Assets:Account1          2 HOOL {100.00 USD, 2015-10-02}
          Assets:Other          -200.00 USD
        """
        postings, balances = self.book_reductions(entries)
        self.assertEqual(inventory.from_string('1 HOOL {100.00 USD, 2015-10-01}, '
                                               '2 HOOL {100.00 USD, 2015-10-02}'),
                         balances['Assets:Account1'])
        self.assertPostingsEqual([
            data.Posting('Assets:Account1', A('2 HOOL'),
                         Cost(D('100.00'), 'USD', datetime.date(2015, 10, 2), None),
                         None, None, None),
            data.Posting('Assets:Other', A('-200.00 USD'), None, None, None, None),
            ], postings)

    @parser.parse_doc(allow_incomplete=True)
    def test_augment__at_cost__different_cost(self, entries, _, __):
        """
        2015-10-01 * "Held-at-cost, positive"
          Assets:Account1          1 HOOL {100.00 USD}
          Assets:Other          -100.00 USD

        2015-10-01 * "Held-at-cost, positive, same cost"
          Assets:Account1          2 HOOL {101.00 USD}
          Assets:Other          -204.00 USD
        """
        postings, balances = self.book_reductions(entries)
        self.assertEqual(inventory.from_string('1 HOOL {100.00 USD, 2015-10-01}, '
                                               '2 HOOL {101.00 USD, 2015-10-01}'),
                         balances['Assets:Account1'])
        self.assertPostingsEqual([
            data.Posting('Assets:Account1', A('2 HOOL'),
                         Cost(D('101.00'), 'USD', datetime.date(2015, 10, 1), None),
                         None, None, None),
            data.Posting('Assets:Other', A('-204.00 USD'), None, None, None, None),
            ], postings)

    @parser.parse_doc(allow_incomplete=True)
    def test_augment__at_cost__different_currency(self, entries, _, __):
        """
        2015-10-01 * "Held-at-cost, positive"
          Assets:Account1          1 HOOL {100.00 USD}
          Assets:Other          -100.00 USD

        2015-10-01 * "Held-at-cost, positive, same cost"
          Assets:Account1          2 HOOL {100.00 CAD}
          Assets:Other          -200.00 CAD
        """
        postings, balances = self.book_reductions(entries)
        self.assertEqual(inventory.from_string('1 HOOL {100.00 USD, 2015-10-01}, '
                                               '2 HOOL {100.00 CAD, 2015-10-01}'),
                         balances['Assets:Account1'])
        self.assertPostingsEqual([
            data.Posting('Assets:Account1', A('2 HOOL'),
                         Cost(D('100.00'), 'CAD', datetime.date(2015, 10, 1), None),
                         None, None, None),
            data.Posting('Assets:Other', A('-200.00 CAD'), None, None, None, None),
            ], postings)

    @parser.parse_doc(allow_incomplete=True)
    def test_augment__at_cost__different_label(self, entries, _, __):
        """
        2015-10-01 * "Held-at-cost, positive"
          Assets:Account1          1 HOOL {100.00 USD}
          Assets:Other          -100.00 USD

        2015-10-01 * "Held-at-cost, positive, same cost"
          Assets:Account1          2 HOOL {100.00 USD, "lot1"}
          Assets:Other          -200.00 USD
        """
        postings, balances = self.book_reductions(entries)
        self.assertEqual(inventory.from_string('1 HOOL {100.00 USD, 2015-10-01}, '
                                               '2 HOOL {100.00 USD, 2015-10-01, "lot1"}'),
                         balances['Assets:Account1'])
        self.assertPostingsEqual([
            data.Posting('Assets:Account1', A('2 HOOL'),
                         Cost(D('100.00'), 'USD', datetime.date(2015, 10, 1), "lot1"),
                         None, None, None),
            data.Posting('Assets:Other', A('-200.00 USD'), None, None, None, None),
            ], postings)

    @parser.parse_doc(allow_incomplete=True)
    def test_reduce__no_cost(self, entries, _, __):
        """
        2015-10-01 * "Held-at-cost, positive"
          Assets:Account1          10 USD
          Assets:Other1           -10 USD

        2015-10-01 * "Held-at-cost, positive, same cost"
          Assets:Account1         -1 USD
          Assets:Other2            1 USD
        """
        _, balances = self.book_reductions(entries)
        self.assertEqual(inventory.from_string('9 USD'),
                         balances['Assets:Account1'])

    @parser.parse_doc(allow_incomplete=True)
    def test_reduce__same_cost(self, entries, _, __):
        """
        2015-10-01 * "Held-at-cost, positive"
          Assets:Account1          3 HOOL {100.00 USD}
          Assets:Other       -300.00 USD

        2015-10-02 * "Held-at-cost, positive, same cost"
          Assets:Account1         -1 HOOL {100.00 USD}
          Assets:Other        100.00 USD
        """
        postings, balances = self.book_reductions(entries)
        self.assertEqual(inventory.from_string('2 HOOL {100.00 USD, 2015-10-01}'),
                         balances['Assets:Account1'])
        self.assertPostingsEqual([
            data.Posting('Assets:Account1', A('-1 HOOL'),
                         Cost(D('100.00'), 'USD', datetime.date(2015, 10, 1), None),
                         None, None, None),
            data.Posting('Assets:Other', A('100.00 USD'), None, None, None, None),
            ], postings)

    @parser.parse_doc(allow_incomplete=True)
    def test_reduce__any_spec(self, entries, _, __):
        """
        2015-10-01 * "Held-at-cost, positive"
          Assets:Account1          3 HOOL {100.00 USD}
          Assets:Other       -300.00 USD

        2015-10-02 * "Held-at-cost, positive, same cost"
          Assets:Account1         -1 HOOL {}
          Assets:Other        100.00 USD
        """
        postings, balances = self.book_reductions(entries)
        self.assertEqual(inventory.from_string('2 HOOL {100.00 USD, 2015-10-01}'),
                         balances['Assets:Account1'])
        self.assertPostingsEqual([
            data.Posting('Assets:Account1', A('-1 HOOL'),
                         Cost(D('100.00'), 'USD', datetime.date(2015, 10, 1), None),
                         None, None, None),
            data.Posting('Assets:Other', A('100.00 USD'), None, None, None, None),
            ], postings)

    @parser.parse_doc(allow_incomplete=True)
    def test_reduce__same_cost__per(self, entries, _, __):
        """
        2015-10-01 * "Held-at-cost, positive"
          Assets:Account1          3 HOOL {100.00 USD}
          Assets:Other       -300.00 USD

        2015-10-02 * "Held-at-cost, positive, same cost"
          Assets:Account1         -1 HOOL {100.00}
          Assets:Other        100.00 USD
        """
        postings, balances = self.book_reductions(entries)
        self.assertEqual(inventory.from_string('2 HOOL {100.00 USD, 2015-10-01}'),
                         balances['Assets:Account1'])
        self.assertPostingsEqual([
            data.Posting('Assets:Account1', A('-1 HOOL'),
                         Cost(D('100.00'), 'USD', datetime.date(2015, 10, 1), None),
                         None, None, None),
            data.Posting('Assets:Other', A('100.00 USD'), None, None, None, None),
            ], postings)

    @parser.parse_doc(allow_incomplete=True)
    def test_reduce__same_cost__total(self, entries, _, __):
        """
        2015-10-01 * "Held-at-cost, positive"
          Assets:Account1          3 HOOL {100.00 USD}
          Assets:Other       -300.00 USD

        2015-10-02 * "Held-at-cost, positive, same cost"
          Assets:Account1         -2 HOOL {# 100.00 USD}
          Assets:Other        200.00 USD
        """
        postings, balances = self.book_reductions(entries)
        self.assertEqual(inventory.from_string('1 HOOL {100.00 USD, 2015-10-01}'),
                         balances['Assets:Account1'])
        self.assertPostingsEqual([
            data.Posting('Assets:Account1', A('-2 HOOL'),
                         Cost(D('100.00'), 'USD', datetime.date(2015, 10, 1), None),
                         None, None, None),
            data.Posting('Assets:Other', A('200.00 USD'), None, None, None, None),
            ], postings)

    @parser.parse_doc(allow_incomplete=True)
    def test_reduce__same_currency(self, entries, _, __):
        """
        2015-10-01 * "Held-at-cost, positive"
          Assets:Account1          3 HOOL {100.00 USD}
          Assets:Other       -300.00 USD

        2015-10-02 * "Held-at-cost, positive, same cost"
          Assets:Account1         -1 HOOL {USD}
          Assets:Other        100.00 USD
        """
        postings, balances = self.book_reductions(entries)
        self.assertEqual(inventory.from_string('2 HOOL {100.00 USD, 2015-10-01}'),
                         balances['Assets:Account1'])
        self.assertPostingsEqual([
            data.Posting('Assets:Account1', A('-1 HOOL'),
                         Cost(D('100.00'), 'USD', datetime.date(2015, 10, 1), None),
                         None, None, None),
            data.Posting('Assets:Other', A('100.00 USD'), None, None, None, None),
            ], postings)

    @parser.parse_doc(allow_incomplete=True)
    def test_reduce__same_date(self, entries, _, __):
        """
        2015-10-01 * "Held-at-cost, positive"
          Assets:Account1          3 HOOL {100.00 USD}
          Assets:Other       -300.00 USD

        2015-10-02 * "Held-at-cost, positive, same cost"
          Assets:Account1         -1 HOOL {2015-10-01}
          Assets:Other        100.00 USD
        """
        postings, balances = self.book_reductions(entries)
        self.assertEqual(inventory.from_string('2 HOOL {100.00 USD, 2015-10-01}'),
                         balances['Assets:Account1'])
        self.assertPostingsEqual([
            data.Posting('Assets:Account1', A('-1 HOOL'),
                         Cost(D('100.00'), 'USD', datetime.date(2015, 10, 1), None),
                         None, None, None),
            data.Posting('Assets:Other', A('100.00 USD'), None, None, None, None),
            ], postings)

    @parser.parse_doc(allow_incomplete=True)
    def test_reduce__same_label(self, entries, _, __):
        """
        2015-10-01 * "Held-at-cost, positive"
          Assets:Account1          3 HOOL {100.00 USD, "6e425dd7b820"}
          Assets:Other       -300.00 USD

        2015-10-02 * "Held-at-cost, positive, same cost"
          Assets:Account1         -1 HOOL {"6e425dd7b820"}
          Assets:Other        100.00 USD
        """
        postings, balances = self.book_reductions(entries)
        self.assertEqual(
            inventory.from_string('2 HOOL {100.00 USD, 2015-10-01, "6e425dd7b820"}'),
            balances['Assets:Account1'])
        self.assertPostingsEqual([
            data.Posting('Assets:Account1', A('-1 HOOL'),
                         Cost(D('100.00'), 'USD', datetime.date(2015, 10, 1), "6e425dd7b820"),
                         None, None, None),
            data.Posting('Assets:Other', A('100.00 USD'), None, None, None, None),
            ], postings)




class TestBooking(unittest.TestCase):
    "Tests the booking & interpolation process."

    maxDiff = 8192

    # def book(self, entry, balances=None, exp_costs=None, debug=False):
    #     if balances is None:
    #         balances = {}
    #     groups, errors = bf.categorize_by_currency(entry, balances)
    #     self.assertFalse(errors)
    #     posting_groups = bf.replace_currencies(entry.postings, groups)
    #     for currency, postings in posting_groups.items():
    #         new_postings, new_balances = bf.book_reductions(postings, balances)
    #         if debug:
    #             for posting in new_postings:
    #                 print(posting)
    #             print(new_balances)

    #         # Check the expected costs.
    #         if exp_costs is not None:
    #             for posting, exp_cost in zip(new_postings, exp_costs):
    #                 self.assertEqual(posting.cost, exp_cost)

        # for balances in {}, {'Assets:Account': inventory.from_string('10 HOOL {99.00 USD}')}:
        #     self.book(entries[0], balances, [
        #         position.CostSpec(D('100.00'), None, 'USD', None, None, False),
        #         None])
        #     self.book(entries[1], balances, [
        #         position.CostSpec(MISSING, None, 'USD', None, None, False),
        #         None])



    # @parser.parse_doc(allow_incomplete=True)
    # def test_augmentation_noop(self, entries, _, options_map):
    #     """
    #     2015-10-01 *
    #       Assets:Account          2 HOOL {100.00 USD}
    #       Assets:Other     -1000.00 USD

    #     2015-10-02 *
    #       Assets:Account          2 HOOL {USD}
    #       Assets:Other     -1000.00 USD
    #     """
    #     # Check that these augmenting legs aren't being touched.
    #     for balances in {}, {'Assets:Account': inventory.from_string('10 HOOL {99.00 USD}')}:
    #         self.book(entries[0], balances, [
    #             position.CostSpec(D('100.00'), None, 'USD', None, None, False),
    #             None])
    #         self.book(entries[1], balances, [
    #             position.CostSpec(MISSING, None, 'USD', None, None, False),
    #             None])

    # @parser.parse_doc(allow_incomplete=True)
    # def test_reduction(self, entries, _, options_map):
    #     """
    #     2015-10-01 *
    #       Assets:Account         -2 HOOL {100.00 USD}
    #       Assets:Other      1000.00 USD
    #     """
    #     balances = {'Assets:Account':
    #                 inventory.from_string('5 HOOL {100.00 USD, 2015-01-01}')}
    #     # FIXME: Bring this back in.
    #     # self.book(entries[0], balances, [
    #     #     position.Cost(D('100.00'), 'USD', datetime.date(2015, 1, 1), None),
    #     #     None], debug=1)


# FIXME: Continue here.
__incomplete__ = True


# class TestFullBooking1(cmptest.TestCase):
#
#     @parser.parse_doc()
#     def __test_categorize_by_currency__ambiguous_cost_no_choice(self, ientries, _, options_map):
#         """
#         ;; Pick the USD lot, because that's all there is in the inventory
#         2015-01-01 *
#           Assets:Bank:Investing          -1 HOOL {}
#           Equity:Opening-Balances       101 USD
#         """
#         groups, free = bf.categorize_by_currency_by_currency(
#             ientries[0].postings, {'USD': I('1 HOOL {100 USD}')})
#         self.assertEqual({'USD': 2}, dictmap(groups, valfun=len))
#         self.assertFalse(free)
#
#     @parser.parse_doc()
#     def __test_categorize_by_currency__ambiguous_cost_choose_lot(self, ientries, _, options_map):
#         """
#         ;; This should know to pick the USD leg because that's the only currency
#         2015-01-01 *
#           Assets:Bank:Investing          -1 HOOL {}
#           Equity:Opening-Balances       101 USD
#         """
#         groups, free = bf.categorize_by_currency_by_currency(
#             ientries[0].postings, {'USD': I('1 HOOL {100 USD}, '
#                                             '1 HOOL {100 CAD}')})
#
#     @parser.parse_doc()
#     def __test_categorize_by_currency__ambiguous_cost_choose_ccy(self, ientries, _, options_map):
#         """
#         ;; Pick the USD lot, because that's all there is in the inventory
#         2015-01-01 *
#           Assets:Bank:Investing          -1 HOOL {}
#           Equity:Opening-Balances       101 USD
#           Equity:Opening-Balances       102 CAD
#         """
#         groups, free = bf.categorize_by_currency_by_currency(
#             ientries[0].postings, {'USD': I('1 HOOL {100 USD}')})
#
#     @parser.parse_doc()
#     def __test_categorize_by_currency__ambiguous_cost_no_choice(self, ientries, _, options_map):
#         """
#         ;; Pick the USD lot, because that's all there is in the inventory
#         2015-01-01 *
#           Assets:Bank:Investing          -1 HOOL {}
#           Equity:Opening-Balances       100 USD
#         """
#         groups, free = bf.categorize_by_currency_by_currency(
#             ientries[0].postings, {'USD': I('1 HOOL {100 USD}')})
#
#     @parser.parse_doc()
#     def __test_categorize_by_currency__ambiguous_cost_with_bal(self, ientries, _, options_map):
#         """
#         ;; This should know to pick the USD leg because that's the only that doesn't already
#         ;; balance from the other postings.
#         2015-01-01 *
#           Assets:Bank:Investing          -1 HOOL {}
#           Equity:Opening-Balances       101 USD
#           Equity:Opening-Balances      -102 CAD
#           Assets:Cash                   102 CAD
#         """
#         groups, free = bf.categorize_by_currency_by_currency(
#             ientries[0].postings, {'USD': I('1 HOOL {100 USD}, '
#                                                '1 HOOL {100 CAD}')})
#
#
# class TestFullBooking2(cmptest.TestCase):
#
#     @loader.load_doc()
#     def __test_full_booking(self, entries, _, options_map):
#         """
#           option "booking_method" "FULL"
#           2013-05-01 open Assets:Bank:Investing
#           2013-05-01 open Equity:Opening-Balances
#
#           2013-05-02 *
#             Assets:Bank:Investing           5 HOOL {501 USD}
#             Equity:Opening-Balances     -2505 USD
#         """
#         self.assertEqual(D('-2505'), entries[-1].postings[-1].units.number)
