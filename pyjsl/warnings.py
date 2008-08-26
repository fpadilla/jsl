# vim: ts=4 sw=4 expandtab
""" This module contains all the warnings. To add a new warning, define a
function. Its name should be in lowercase and words should be separated by
underscores.

The function should be decorated with a @lookfor call specifying the nodes it
wants to examine. The node names may be in the tok.KIND or (tok.KIND, op.OPCODE)
format. To report a warning, the function should raise a LintWarning exception.

For example:

    @lookfor(tok.NODEKIND, (tok.NODEKIND, op.OPCODE))
    def warning_name(node):
        if questionable:
            raise LintWarning, node
"""
import re
import sys
import types

import util
import visitation
from pyspidermonkey import tok, op

# TODO: document inspect, node:opcode, etc

warnings = {
    'comparison_type_conv': 'comparisons against null, 0, true, false, or an empty string allowing implicit type conversion (use === or !==)',
    'default_not_at_end': 'the default case is not at the end of the switch statement',
    'duplicate_case_in_switch': 'duplicate case in switch statement',
    'missing_default_case': 'missing default case in switch statement',
    'with_statement': 'with statement hides undeclared variables; use temporary variable instead',
    'useless_comparison': 'useless comparison; comparing identical expressions',
    'use_of_label': 'use of label',
    'misplaced_regex': 'regular expressions should be preceded by a left parenthesis, assignment, colon, or comma',
    'assign_to_function_call': 'assignment to a function call',
    'ambiguous_else_stmt': 'the else statement could be matched with one of multiple if statements (use curly braces to indicate intent',
    'block_without_braces': 'block statement without curly braces',
    'ambiguous_nested_stmt': 'block statements containing block statements should use curly braces to resolve ambiguity',
    'inc_dec_within_stmt': 'increment (++) and decrement (--) operators used as part of greater statement',
    'comma_separated_stmts': 'multiple statements separated by commas (use semicolons?)',
    'empty_statement': 'empty statement or extra semicolon',
    'missing_break': 'missing break statement',
    'missing_break_for_last_case': 'missing break statement for last case in switch',
    'multiple_plus_minus': 'unknown order of operations for successive plus (e.g. x+++y) or minus (e.g. x---y) signs',
    'useless_assign': 'useless assignment',
    'unreachable_code': 'unreachable code',
    'meaningless_block': 'meaningless block; curly braces have no impact',
    'useless_void': 'use of the void type may be unnecessary (void is always undefined)',
    'parseint_missing_radix': 'parseInt missing radix parameter',
    'leading_decimal_point': 'leading decimal point may indicate a number or an object member',
    'trailing_decimal_point': 'trailing decimal point may indicate a number or an object member',
    'octal_number': 'leading zeros make an octal number',
    'trailing_comma_in_array': 'extra comma is not recommended in array initializers',
    'useless_quotes': 'the quotation marks are unnecessary',
    'mismatch_ctrl_comments': 'mismatched control comment; "ignore" and "end" control comments must have a one-to-one correspondence',
    'redeclared_var': 'redeclaration of {0} {1}',
    'undeclared_identifier': 'undeclared identifier: {0}',
    'unreferenced_identifier': 'identifier is declared but never referenced: {0}',
    'jsl_cc_not_understood': 'couldn\'t understand control comment using /*jsl:keyword*/ syntax',
    'nested_comment': 'nested comment',
    'legacy_cc_not_understood': 'couldn\'t understand control comment using /*@keyword@*/ syntax',
    'var_hides_arg': 'variable {0} hides argument',
    'duplicate_formal': 'TODO',
    'missing_semicolon': 'missing semicolon',
    'ambiguous_newline': 'unexpected end of line; it is ambiguous whether these lines are part of the same statement',
    'missing_option_explicit': 'the "option explicit" control comment is missing',
    'partial_option_explicit': 'the "option explicit" control comment, if used, must be in the first script tag',
    'dup_option_explicit': 'duplicate "option explicit" control comment',
    'invalid_fallthru': 'unexpected "fallthru" control comment',
    'invalid_pass': 'unexpected "pass" control comment'
}

_visitors = []
def lookfor(*args):
    def decorate(fn):
        fn.warning = fn.func_name.rstrip('_')
        assert fn.warning in warnings, 'Missing warning description: %s' % fn.warning

        for arg in args:
            _visitors.append((arg, fn))
    return decorate

class LintWarning(Exception):
    def __init__(self, node):
        self.node = node

def _get_branch_in_for(node):
        " Returns None if this is not one of the branches in a 'for' "
        if node.parent and node.parent.kind == tok.RESERVED and \
            node.parent.parent.kind == tok.FOR:
            return node.node_index
        return None

def _get_exit_points(node):
    if node.kind == tok.LC:
        # Only if the last child contains it
        exit_points = set([None])
        for kid in node.kids:
            # "None" is only a valid exit point for the last statement.
            if None in exit_points:
                exit_points.remove(None)
            if kid:
                exit_points |= _get_exit_points(kid)
    elif node.kind == tok.IF:
        # Only if both branches have an exit point
        cond_, if_, else_ = node.kids
        exit_points = _get_exit_points(if_)
        if else_:
            exit_points |= _get_exit_points(else_)
    elif node.kind == tok.SWITCH:
        exit_points = set([None])

        switch_has_default = False
        switch_has_final_fallthru = True

        switch_var, switch_stmts = node.kids
        for node in switch_stmts.kids:
            case_val, case_stmt = node.kids
            case_exit_points = _get_exit_points(case_stmt)
            switch_has_default = switch_has_default or node.kind == tok.DEFAULT
            switch_has_final_fallthru = None in case_exit_points
            exit_points |= case_exit_points

        # Correct the "None" exit point.
        exit_points.remove(None)

        # Check if the switch contained any break
        if tok.BREAK in exit_points:
            exit_points.remove(tok.BREAK)
            exit_points.add(None)

        # Check if the switch had a default case
        if not switch_has_default:
            exit_points.add(None)

        # Check if the final case statement had a fallthru
        if switch_has_final_fallthru:
            exit_points.add(None)
    elif node.kind == tok.BREAK:
        exit_points = set([tok.BREAK])
    elif node.kind == tok.WITH:
        exit_points = _get_exit_points(node.kids[-1])
    elif node.kind == tok.RETURN:
        exit_points = set([tok.RETURN])
    elif node.kind == tok.THROW:
        exit_points = set([tok.THROW])
    elif node.kind == tok.TRY:
        try_, catch_, finally_ = node.kids

        assert catch_.kind == tok.RESERVED
        catch_, = catch_.kids
        assert catch_.kind == tok.LEXICALSCOPE
        catch_, = catch_.kids
        assert catch_.kind == tok.CATCH
        ignored, ignored, catch_ = catch_.kids
        assert catch_.kind == tok.LC

        exit_points = _get_exit_points(try_) | _get_exit_points(catch_)
        if finally_:
            finally_exit_points = _get_exit_points(finally_)
            if None in finally_exit_points:
                # The finally statement does not add a missing exit point.
                finally_exit_points.remove(None)
            else:
                # If the finally statement always returns, the other
                # exit points are irrelevant.
                if None in exit_points:
                    exit_points.remove(None)

            exit_points |= finally_exit_points

    else:
        exit_points = set([None])

    return exit_points

@lookfor((tok.EQOP, op.EQ))
def comparison_type_conv(node):
    for kid in node.kids:
        if kid.kind == tok.PRIMARY and kid.opcode in (op.NULL, op.TRUE, op.FALSE):
            raise LintWarning, kid
        if kid.kind == tok.NUMBER and not kid.dval:
            raise LintWarning, kid
        if kid.kind == tok.STRING and not kid.atom:
            raise LintWarning, kid

@lookfor(tok.DEFAULT)
def default_not_at_end(node):
    siblings = node.parent.kids
    if node.node_index != len(siblings)-1:
        raise LintWarning, siblings[node.node_index+1]

@lookfor(tok.CASE)
def duplicate_case_in_switch(node):
    # Only look at previous siblings
    siblings = node.parent.kids
    siblings = siblings[:node.node_index]
    # Compare values (first kid)
    node_value = node.kids[0]
    for sibling in siblings:
        if sibling.kind == tok.CASE:
            sibling_value = sibling.kids[0]
            if node_value.is_equivalent(sibling_value, True):
                raise LintWarning, node

@lookfor(tok.SWITCH)
def missing_default_case(node):
    value, cases = node.kids
    for case in cases.kids:
        if case.kind == tok.DEFAULT:
            return
    raise LintWarning, node

@lookfor(tok.WITH)
def with_statement(node):
    raise LintWarning, node

@lookfor(tok.EQOP,tok.RELOP)
def useless_comparison(node):
    lvalue, rvalue = node.kids
    if lvalue.is_equivalent(rvalue):
        raise LintWarning, node

@lookfor((tok.COLON, op.NAME))
def use_of_label(node):
    raise LintWarning, node

@lookfor((tok.OBJECT, op.REGEXP))
def misplaced_regex(node):
    if node.parent.kind == tok.NAME and node.parent.opcode == op.SETNAME:
        return # Allow in var statements
    if node.parent.kind == tok.ASSIGN and node.parent.opcode == op.NOP:
        return # Allow in assigns
    if node.parent.kind == tok.COLON and node.parent.parent.kind == tok.RC:
        return # Allow in object literals
    if node.parent.kind == tok.LP and node.parent.opcode == op.CALL:
        return # Allow in parameters
    if node.parent.kind == tok.DOT and node.parent.opcode == op.GETPROP:
        return # Allow in /re/.property
    if node.parent.kind == tok.RETURN:
        return # Allow for return values
    raise LintWarning, node

@lookfor(tok.ASSIGN)
def assign_to_function_call(node):
    if node.kids[0].kind == tok.LP:
        raise LintWarning, node

@lookfor(tok.IF)
def ambiguous_else_stmt(node):
    # Only examine this node if it has an else statement.
    condition, if_, else_ = node.kids
    if not else_:
        return

    tmp = node
    while tmp:
        # Curly braces always clarify if statements.
        if tmp.kind == tok.LC:
            return
        # Else is only ambiguous in the first branch of an if statement.
        if tmp.parent.kind == tok.IF and tmp.node_index == 1:
            raise LintWarning, else_
        tmp = tmp.parent

@lookfor(tok.IF, tok.WHILE, tok.DO, tok.FOR, tok.WITH)
def block_without_braces(node):
    if node.kids[1].kind != tok.LC:
        raise LintWarning, node.kids[1]

_block_nodes = (tok.IF, tok.WHILE, tok.DO, tok.FOR, tok.WITH)
@lookfor(*_block_nodes)
def ambiguous_nested_stmt(node):
    # Ignore "else if"
    if node.kind == tok.IF and node.node_index == 2 and node.parent.kind == tok.IF:
        return

    # If the parent is a block, it means a block statement
    # was inside a block statement without clarifying curlies.
    # (Otherwise, the node type would be tok.LC.)
    if node.parent.kind in _block_nodes:
        raise LintWarning, node

@lookfor(tok.INC, tok.DEC)
def inc_dec_within_stmt(node):
    if node.parent.kind == tok.SEMI:
        return

    # Allow within the third part of the "for"
    tmp = node
    while tmp and tmp.parent and tmp.parent.kind == tok.COMMA:
        tmp = tmp.parent
    if tmp and tmp.node_index == 2 and \
        tmp.parent.kind == tok.RESERVED and \
        tmp.parent.parent.kind == tok.FOR:
        return

    raise LintWarning, node

@lookfor(tok.COMMA)
def comma_separated_stmts(node):
    # Allow within the first and third part of "for(;;)"
    if _get_branch_in_for(node) in (0, 2):
        return
    # This is an array
    if node.parent.kind == tok.RB:
        return
    raise LintWarning, node

@lookfor(tok.SEMI)
def empty_statement(node):
    if not node.kids[0]:
        raise LintWarning, node
@lookfor(tok.LC)
def empty_statement_(node):
    if node.kids:
        return
    # Ignore the outermost block.
    if not node.parent:
        return
    # Some empty blocks are meaningful.
    if node.parent.kind in (tok.CATCH, tok.CASE, tok.DEFAULT, tok.SWITCH, tok.FUNCTION):
        return
    raise LintWarning, node

@lookfor(tok.CASE, tok.DEFAULT)
def missing_break(node):
    # The last item is handled separately
    if node.node_index == len(node.parent.kids)-1:
        return
    case_contents = node.kids[1]
    assert case_contents.kind == tok.LC
    # Ignore empty case statements
    if not case_contents.kids:
        return
    if None in _get_exit_points(case_contents):
        # Show the warning on the *next* node.
        raise LintWarning, node.parent.kids[node.node_index+1]

@lookfor(tok.CASE, tok.DEFAULT)
def missing_break_for_last_case(node):
    if node.node_index < len(node.parent.kids)-1:
        return
    case_contents = node.kids[1]
    assert case_contents.kind == tok.LC
    if None in _get_exit_points(case_contents):
        raise LintWarning, node

@lookfor(tok.INC)
def multiple_plus_minus(node):
    if node.node_index == 0 and node.parent.kind == tok.PLUS:
        raise LintWarning, node
@lookfor(tok.DEC)
def multiple_plus_minus_(node):
    if node.node_index == 0 and node.parent.kind == tok.MINUS:
        raise LintWarning, node

@lookfor((tok.NAME, op.SETNAME))
def useless_assign(node):
    if node.parent.kind == tok.ASSIGN:
        assert node.node_index == 0
        value = node.parent.kids[1]
    elif node.parent.kind == tok.VAR:
        value = node.kids[0]
    if value and value.kind == tok.NAME and node.atom == value.atom:
        raise LintWarning, node

@lookfor(tok.BREAK, tok.CONTINUE, tok.RETURN, tok.THROW)
def unreachable_code(node):
    if node.parent.kind == tok.LC and \
        node.node_index != len(node.parent.kids)-1:
        raise LintWarning, node.parent.kids[node.node_index+1]

#TODO: @lookfor(tok.IF)
def meaningless_block(node):
    condition, if_, else_ = node.kids
    if condition.kind == tok.PRIMARY and condition.opcode in (op.TRUE, op.FALSE, op.NULL):
        raise LintWarning, condition
#TODO: @lookfor(tok.WHILE)
def meaningless_blocK_(node):
    condition = node.kids[0]
    if condition.kind == tok.PRIMARY and condition.opcode in (op.FALSE, op.NULL):
        raise LintWarning, condition
@lookfor(tok.LC)
def meaningless_block__(node):
    if node.parent and node.parent.kind == tok.LC:
        raise LintWarning, node

@lookfor((tok.UNARYOP, op.VOID))
def useless_void(node):
    raise LintWarning, node

@lookfor((tok.LP, op.CALL))
def parseint_missing_radix(node):
    if node.kids[0].kind == tok.NAME and node.kids[0].atom == 'parseInt' and len(node.kids) <= 2:
        raise LintWarning, node

@lookfor(tok.NUMBER)
def leading_decimal_point(node):
    if node.atom.startswith('.'):
        raise LintWarning, node

@lookfor(tok.NUMBER)
def trailing_decimal_point(node):
    if node.parent.kind == tok.DOT:
        raise LintWarning, node
    if node.atom.endswith('.'):
        raise LintWarning, node

_octal_regexp = re.compile('^0[0-9]')
@lookfor(tok.NUMBER)
def octal_number(node):
    if _octal_regexp.match(node.atom):
        raise LintWarning, node

@lookfor(tok.RB)
def trailing_comma_in_array(node):
    if node.end_comma:
        raise LintWarning, node

@lookfor(tok.STRING)
def useless_quotes(node):
    if node.node_index == 0 and node.parent.kind == tok.COLON:
        # Only warn if the quotes could safely be removed.
        if util.isidentifier(node.atom):
            raise LintWarning, node

@lookfor()
def mismatch_ctrl_comments(node):
    pass

@lookfor()
def redeclared_var(node):
    pass

@lookfor()
def undeclared_identifier(node):
    pass

@lookfor()
def jsl_cc_not_understood(node):
    pass

@lookfor()
def nested_comment(node):
    pass

@lookfor()
def legacy_cc_not_understood(node):
    pass

@lookfor()
def var_hides_arg(node):
    pass

@lookfor()
def duplicate_formal(node):
    pass

@lookfor()
def missing_semicolon(node):
    pass

@lookfor()
def ambiguous_newline(node):
    pass

@lookfor()
def missing_option_explicit(node):
    pass

@lookfor()
def partial_option_explicit(node):
    pass

@lookfor()
def dup_option_explicit(node):
    pass

def make_visitors():
    visitors = {}
    for kind, func in _visitors:
        try:
            visitors[kind].append(func)
        except KeyError:
            visitors[kind] = [func]
    return visitors

