def init():
    vex.observe('open_project', on_open_project)

def find_logging_call(node):
    for previous_sibling in node.previous_siblings():
        if str(previous_sibling).startswith('logger.'):
            return previous_sibling
    return None

def find_parent(node, predicate):
    parent = node.parent().parent()
    if predicate(parent):
        return parent
    return None

def on_open_project(_):
    vex.search(
        'python',
        '(return_statement) @return_statement',
        on_match,
    )

def on_match(event):
    return_statement = event.captures['return_statement']

    parent = find_parent(
        return_statement,
        lambda node: node.kind == 'if_statement',
    )

    if type(parent) == type(None):
        return

    logging_call = find_logging_call(return_statement)
    if logging_call:
        return

    vex.warn(
        'logging',
        'return statement without logging call',
        at=(parent, 'consider adding the logging call')
    )
