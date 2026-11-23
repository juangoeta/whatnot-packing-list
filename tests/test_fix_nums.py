"""fix_nums joins whisper's split listing numbers ("3.66", "3-11", "2 22", "1 0 2") and leaves prices and clock times alone."""
import run

def test_fix_nums_joins_split_listing_numbers():
    assert run.fix_nums('number 3.66 for amy') == 'number 366 for amy'
    assert run.fix_nums('$3.63 sold') == '363 sold'            # Oct 9, 2026: whisper wrote #363 as a price
    assert run.fix_nums('number 3-11 and 1/24') == 'number 311 and 124'
    assert run.fix_nums('2 22 for joe and 1 0 2') == '222 for joe and 102'

def test_fix_nums_leaves_prices_and_times_alone():
    assert run.fix_nums('that is $1.50 and 2.99 and $3.00') == 'that is $1.50 and 2.99 and $3.00'
    assert run.fix_nums('3.50 each, $12.50 for the set') == '3.50 each, $12.50 for the set'
    assert run.fix_nums('at 3 30 pm and 2 15pm') == 'at 3 30 pm and 2 15pm'
    assert run.fix_nums('4.99') == '4.99' and run.fix_nums('1.500') == '1.500'
