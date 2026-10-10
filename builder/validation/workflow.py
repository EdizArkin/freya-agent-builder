"""Local checks of a generated Freya workflow, before anything is sent to the platform."""
from builder.validation.safety import leaks


def check_workflow(workflow):
    """Local checks for the rules in the kit's workflow-format.md and tools-and-code.md."""
    bad = []
    nodes = workflow.get('nodes') or {}

    def walk(value, path):
        if value is None:
            bad.append('null value at ' + path)
        elif isinstance(value, dict):
            for k, v in value.items():
                walk(v, path + '.' + str(k))
        elif isinstance(value, list):
            for i, v in enumerate(value):
                walk(v, path + '[' + str(i) + ']')
    walk(workflow, '$')
    bad.extend('credential leak at ' + leak for leak in leaks(workflow))
    if workflow.get('initial_node') not in nodes:
        bad.append('initial_node does not exist')
    if 'agent_config' in workflow:
        bad.append('agent_config must be left out')
    seen, queue = set(), [workflow.get('initial_node')]
    while queue:
        name = queue.pop()
        if name in seen or name not in nodes:
            continue
        seen.add(name)
        queue += [t.get('target_node') for t in nodes[name].get('transitions') or []]
    for name, node in nodes.items():
        kind = node.get('node_type')
        if node.get('name') != name:
            bad.append(name + ': name must equal the node key')
        if name not in seen:
            bad.append(name + ': unreachable')
        transitions = node.get('transitions') or []
        for t in transitions:
            if t.get('target_node') not in nodes:
                bad.append(name + ': transition to unknown node ' + str(t.get('target_node')))
            cond = t.get('condition') or {}
            if 'logic' in cond and cond['logic'] != 'or':
                bad.append(name + ': top-level condition logic must be or')
        if kind == 'conversation':
            if node.get('pre_actions'):
                bad.append(name + ': pre_actions are stripped from conversation nodes')
            if not transitions:
                bad.append(name + ': conversation node cannot be left')
        elif kind in ('code', 'api_request'):
            if node.get('tools'):
                bad.append(name + ': tools are stripped from non-conversation nodes')
            if not node.get('passthrough') or not transitions or transitions[-1].get('condition', {}).get('operator') != 'always':
                bad.append(name + ': passthrough node must end with an always transition')
        elif kind == 'end_call':
            if transitions or not node.get('terminal'):
                bad.append(name + ': end_call needs a terminal and no transitions')
        else:
            bad.append(name + ': unsupported node_type ' + str(kind))
        for t in (node.get('tools') or []) + (node.get('pre_actions') or []):
            fn = t.get('function') or {}
            props = (fn.get('parameters') or {}).get('properties') or {}
            if fn.get('handler') == '__function__:api_call':
                if not str((props.get('url') or {}).get('default', '')).startswith('https://'):
                    bad.append(name + ': api_call ' + str(fn.get('name')) + ' has no baked https url')
                for field, leaf in ((props.get('body') or {}).get('properties') or {}).items():
                    if 'default' not in leaf or 'const' in leaf:
                        bad.append(name + ': body field ' + field + ' must use default, never const')
            for key in ('pre_process_code', 'post_process_code'):
                if key in t and ('{{' in t[key] or '}}' in t[key]):
                    bad.append(name + ': double brace in ' + key)
            code = (props.get('code') or {}).get('default')
            if fn.get('handler') == '__function__:code_exec' and (not code or '{{' in code or '}}' in code):
                bad.append(name + ': code node needs template-free code')
    if not any(n.get('node_type') == 'end_call' for n in nodes.values()):
        bad.append('no end_call node')
    return bad
