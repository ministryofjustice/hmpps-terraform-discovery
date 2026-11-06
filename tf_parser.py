"""Pure-Python Terraform loader.

A drop-in replacement for tfparse's ``load_from_path`` (which wraps a Go
c-shared library that cannot be loaded on musl/Alpine). Files are parsed with
python-hcl2 and a small evaluator resolves the subset of Terraform semantics
that terraform discovery relies on: variable defaults, locals, references to
resources and module arguments, string templates, operators, conditionals, a
handful of built-in functions and ``count``/``for_each`` expansion.

Output mirrors tfparse: a dict keyed by block type (``module``, or the resource
type for ``resource`` blocks), each a list of attribute dicts with a
``__tfmeta`` entry. Values that cannot be resolved follow tfparse conventions:

* an unresolvable reference used directly as an attribute value becomes
  ``{'__attribute__': 'var.x', '__name__': 'x', ...}``
* unresolvable values nested in lists/objects, or used in operators and
  conditionals, become ``None``
* a template or function call that cannot be fully evaluated is returned as
  its original source text, e.g. ``'${var.a}-suffix'``
"""

import os
import re

import hcl2
from lark import Token, Tree
from lark.exceptions import LarkError


class ParseError(Exception):
  pass


class Unresolved:
  """A reference that cannot be resolved from the local configuration."""

  def __init__(self, attribute, name, **extra):
    self.attribute = attribute
    self.name = name
    self.extra = extra

  def to_json(self):
    return {'__attribute__': self.attribute, '__name__': self.name, **self.extra}


class _Partial:
  """An expression (template, function call) that cannot be fully evaluated."""


_PARTIAL = _Partial()


class _Ref:
  """An unevaluated traversal such as ``var.name`` or ``aws_x.y.attr``."""

  def __init__(self, parts):
    self.parts = parts


def _is_unknown(value):
  return isinstance(value, (Unresolved, _Partial))


def _regex_or_replace(string, search, replacement):
  if len(search) > 1 and search[0] == search[-1] == '/':
    return re.sub(search[1:-1], re.sub(r'\$(\d+)', r'\\\1', replacement), string)
  return string.replace(search, replacement)


def _format(fmt, *args):
  args = iter(args)
  return re.sub(
    r'%%|%[-+# 0]*\d*(?:\.\d+)?[sdvfqt]',
    lambda m: '%' if m.group(0) == '%%' else _to_template_string(next(args)),
    fmt,
  )


FUNCTIONS = {
  'coalesce': lambda *xs: next((x for x in xs if x not in (None, '')), None),
  'concat': lambda *lists: [x for lst in lists for x in lst],
  'contains': lambda xs, x: x in xs,
  'element': lambda xs, i: xs[i % len(xs)],
  'format': _format,
  'join': lambda sep, xs: sep.join(_to_template_string(x) for x in xs),
  'length': len,
  'lookup': lambda m, k, *default: m[k] if k in m or not default else default[0],
  'lower': str.lower,
  'merge': lambda *maps: {k: v for m in maps for k, v in m.items()},
  'replace': _regex_or_replace,
  'split': lambda sep, s: s.split(sep),
  'substr': lambda s, offset, length: s[
    offset : None if length < 0 else offset + length
  ],
  'tolist': list,
  'tonumber': lambda x: (
    x if isinstance(x, (int, float)) else float(x) if '.' in x else int(x)
  ),
  'toset': lambda xs: list(dict.fromkeys(xs)),
  'tostring': lambda x: _to_template_string(x),
  'trimspace': str.strip,
  'upper': str.upper,
}

# HCL operator precedence, highest binds tightest.
_PRECEDENCE = {
  '*': 6,
  '/': 6,
  '%': 6,
  '+': 5,
  '-': 5,
  '>': 4,
  '>=': 4,
  '<': 4,
  '<=': 4,
  '==': 3,
  '!=': 3,
  '&&': 2,
  '||': 1,
}

_OPERATORS = {
  '*': lambda a, b: a * b,
  '/': lambda a, b: a / b,
  '%': lambda a, b: a % b,
  '+': lambda a, b: a + b,
  '-': lambda a, b: a - b,
  '>': lambda a, b: a > b,
  '>=': lambda a, b: a >= b,
  '<': lambda a, b: a < b,
  '<=': lambda a, b: a <= b,
  '==': lambda a, b: a == b,
  '!=': lambda a, b: a != b,
  '&&': lambda a, b: a and b,
  '||': lambda a, b: a or b,
}


def _to_template_string(value):
  if value is None:
    return ''
  if isinstance(value, bool):
    return str(value).lower()
  return str(value)


def _children(node):
  return [
    c
    for c in node.children
    if not (isinstance(c, Tree) and c.data == 'new_line_or_comment')
  ]


def _identifier(node):
  return str(node.children[0])


_ESCAPES = {'n': '\n', 'r': '\r', 't': '\t', '"': '"', '\\': '\\'}
_ESCAPE_RE = re.compile(
  r'\\(?:([nrt"\\])|u([0-9a-fA-F]{4})|U([0-9a-fA-F]{8}))|([$%])\4\{'
)


def _unescape(text):
  """Decode HCL quoted-string escapes, including ``$${`` and ``%%{``."""

  def replace(m):
    simple, u4, u8, marker = m.groups()
    if simple:
      return _ESCAPES[simple]
    if marker:
      return marker + '{'
    return chr(int(u4 or u8, 16))

  return _ESCAPE_RE.sub(replace, text)


def _string_literal(node):
  return _unescape(''.join(str(t) for part in node.children for t in part.children))


class _Evaluator:
  def __init__(self, source, variables, locals_, resources, modules):
    self.source = source
    self.variables = variables
    self.locals = locals_
    self.resources = resources
    self.modules = modules
    self.count = None
    self.each = None
    self._resolving_locals = set()

  def for_instance(self, count=None, each=None):
    evaluator = _Evaluator(
      self.source, self.variables, self.locals, self.resources, self.modules
    )
    evaluator.count, evaluator.each = count, each
    return evaluator

  def value(self, node):
    """Fully evaluate an expression node, resolving any references."""
    return self.resolve(self.eval(node))

  def eval(self, node):
    if isinstance(node, Token):
      return str(node)
    handler = getattr(self, f'_eval_{node.data}', None)
    return handler(node) if handler else _PARTIAL

  def _eval_expr_term(self, node):
    inner = [c for c in _children(node) if not isinstance(c, Token)]
    return self.eval(inner[0])

  def _eval_int_lit(self, node):
    return int(''.join(str(t) for t in node.children))

  def _eval_float_lit(self, node):
    return float(''.join(str(t) for t in node.children))

  def _eval_identifier(self, node):
    name = _identifier(node)
    if name in ('true', 'false', 'null'):
      return {'true': True, 'false': False, 'null': None}[name]
    return _Ref([name])

  def _eval_string(self, node):
    parts = []
    for part in node.children:
      for piece in part.children:
        if isinstance(piece, Tree) and piece.data == 'interpolation':
          parts.append(self.value(piece.children[0]))
        else:
          parts.append(_unescape(str(piece)))
    if any(_is_unknown(p) for p in parts):
      return _PARTIAL
    if len(parts) == 1 and not isinstance(parts[0], str):
      return parts[0]
    return ''.join(_to_template_string(p) for p in parts)

  def _eval_heredoc_template(self, node):
    return str(node.children[0]).split('\n', 1)[1].rsplit('\n', 2)[0] + '\n'

  def _eval_heredoc_template_trim(self, node):
    lines = self._eval_heredoc_template(node).split('\n')
    indent = min((len(x) - len(x.lstrip()) for x in lines if x.strip()), default=0)
    return '\n'.join(x[indent:] for x in lines)

  def _eval_tuple(self, node):
    return [self.eval(c) for c in _children(node) if isinstance(c, Tree)]

  def _eval_object(self, node):
    result = {}
    for element in _children(node):
      if not (isinstance(element, Tree) and element.data == 'object_elem'):
        continue
      key_node, value_node = element.children[0].children[0], element.children[-1]
      key = self.eval(key_node)
      if isinstance(key, _Ref) and len(key.parts) == 1:
        key = key.parts[0]
      else:
        key = self.resolve(key)
      result[key] = self.eval(value_node)
    return result

  def _eval_get_attr_expr_term(self, node):
    base_node, get_attr = _children(node)
    name = _identifier(get_attr.children[0])
    base = self.eval(base_node)
    if isinstance(base, _Ref):
      return _Ref(base.parts + [name])
    base = self.resolve(base)
    if isinstance(base, dict) and name in base:
      return base[name]
    return _PARTIAL

  def _eval_index_expr_term(self, node):
    base_node, index = _children(node)
    base = self.value(base_node)
    key = self.value(_children(index)[0])
    try:
      return base[key]
    except (KeyError, IndexError, TypeError):
      return _PARTIAL

  def _eval_function_call(self, node):
    children = _children(node)
    name = _identifier(children[0])
    args = []
    if len(children) > 1:
      args = [self.value(a) for a in _children(children[1]) if isinstance(a, Tree)]
    if name not in FUNCTIONS or any(_is_unknown(a) for a in args):
      return _PARTIAL
    try:
      return FUNCTIONS[name](*args)
    except Exception:
      return _PARTIAL

  def _eval_unary_op(self, node):
    operator, operand = node.children[0], _children(node)[-1]
    value = self.value(operand)
    if _is_unknown(value):
      return None
    return not value if str(operator) == '!' else -value

  def _eval_conditional(self, node):
    condition, if_true, if_false = _children(node)
    return self._conditional(self.value(condition), if_true, if_false)

  def _conditional(self, condition, if_true, if_false):
    if _is_unknown(condition) or condition is None:
      return None
    return self.eval(if_true if condition else if_false)

  def _eval_binary_op(self, node):
    # python-hcl2 parses operator chains right-recursively with no precedence,
    # and binds a trailing conditional tighter than the operators. Flatten the
    # chain and re-apply HCL precedence.
    operands, operators = [], []
    current = node
    while isinstance(current, Tree) and current.data == 'binary_op':
      left, term = _children(current)[:2]
      operands.append(left)
      term_children = _children(term)
      operators.append(str(term_children[0].children[0]))
      current = term_children[-1]

    conditional = None
    if isinstance(current, Tree) and current.data == 'conditional':
      conditional = _children(current)
      current = conditional[0]
    operands.append(current)

    result = self._apply_precedence([self.value(o) for o in operands], operators)
    if conditional:
      return self._conditional(result, conditional[1], conditional[2])
    return result

  def _apply_precedence(self, values, operators):
    for level in sorted(set(_PRECEDENCE.values()), reverse=True):
      i = 0
      while i < len(operators):
        if _PRECEDENCE[operators[i]] != level:
          i += 1
          continue
        left, right = values[i], values[i + 1]
        if _is_unknown(left) or _is_unknown(right) or None in (left, right):
          combined = None
        else:
          try:
            combined = _OPERATORS[operators[i]](left, right)
          except Exception:
            combined = None
        values[i : i + 2] = [combined]
        del operators[i]
    return values[0]

  def resolve(self, value):
    if not isinstance(value, _Ref):
      return value
    parts = value.parts
    root = parts[0]

    if root == 'var' and len(parts) >= 2:
      if parts[1] not in self.variables:
        return Unresolved(f'var.{parts[1]}', parts[1])
      return self._traverse(self.variables[parts[1]], parts[2:])

    if root == 'local' and len(parts) >= 2:
      name = parts[1]
      if name not in self.locals or name in self._resolving_locals:
        return Unresolved(f'local.{name}', name)
      self._resolving_locals.add(name)
      try:
        return self._traverse(self.value(self.locals[name]), parts[2:])
      finally:
        self._resolving_locals.discard(name)

    if root == 'count' and self.count is not None:
      return self.count
    if root == 'each' and self.each is not None and len(parts) >= 2:
      key, val = self.each
      return key if parts[1] == 'key' else self._traverse(val, parts[2:])

    if root == 'module' and len(parts) >= 2:
      # Module outputs aren't available, but discovery frequently reads a value
      # that was passed into another module, so resolve to that argument.
      arguments = self.modules.get(parts[1], {})
      if len(parts) >= 3 and parts[2] in arguments:
        return self._traverse(self.value(arguments[parts[2]]), parts[3:])
      return Unresolved('.'.join(parts), parts[1])

    if root == 'data' and len(parts) >= 3:
      return Unresolved(
        '.'.join(parts),
        parts[2],
        __ref__=f'{parts[1]}.{parts[2]}',
        __type__=parts[1],
      )

    if len(parts) >= 3 and (root, parts[1]) in self.resources:
      arguments = self.resources[(root, parts[1])]
      if parts[2] in arguments:
        return self._traverse(self.value(arguments[parts[2]]), parts[3:])
      return Unresolved(
        '.'.join(parts), parts[1], __ref__=f'{root}.{parts[1]}', __type__=root
      )

    return Unresolved('.'.join(parts), parts[1] if len(parts) > 1 else root)

  def _traverse(self, value, attributes):
    for attribute in attributes:
      if isinstance(value, dict) and attribute in value:
        value = self.resolve(value[attribute])
      else:
        return _PARTIAL
    return value

  def to_json(self, value, top_level=True):
    value = self.resolve(value)
    if isinstance(value, Unresolved):
      return value.to_json() if top_level else None
    if isinstance(value, _Partial):
      return None
    if isinstance(value, list):
      return [self.to_json(v, top_level=False) for v in value]
    if isinstance(value, dict):
      return {k: self.to_json(v, top_level=False) for k, v in value.items()}
    return value

  def attribute_json(self, filename, node):
    value = self.value(node)
    if isinstance(value, _Partial):
      return _source_text(self.source[filename], node)
    return self.to_json(value)


def _source_text(source, node):
  text = source[node.meta.start_pos : node.meta.end_pos].strip()
  if len(text) >= 2 and text[0] == text[-1] == '"':
    return text[1:-1]
  return '${' + text + '}'


def _block_parts(block):
  children = _children(block)
  kind = _identifier(children[0])
  labels = []
  body = None
  for child in children[1:]:
    if child.data == 'identifier':
      labels.append(_identifier(child))
    elif child.data == 'string':
      labels.append(_string_literal(child))
    elif child.data == 'body':
      body = child
  arguments = {
    _identifier(c.children[0]): c.children[-1]
    for c in body.children
    if isinstance(c, Tree) and c.data == 'attribute'
  }
  return kind, labels, arguments


def _parse_files(path):
  source, blocks = {}, []
  for filename in sorted(os.listdir(path)):
    full_path = os.path.join(path, filename)
    if not filename.endswith('.tf') or not os.path.isfile(full_path):
      continue
    with open(full_path, encoding='utf-8') as f:
      # python-hcl2 fails on a trailing comment without a final newline
      text = f.read() + '\n'
    try:
      tree = hcl2.parses(text)
    except LarkError as e:
      raise ParseError(f'{full_path}: {e}') from e
    source[filename] = text
    for block in tree.children[0].children:
      if isinstance(block, Tree) and block.data == 'block':
        blocks.append((filename, block))
  return source, blocks


def _instances(evaluator, arguments):
  if 'count' in arguments:
    count = evaluator.value(arguments['count'])
    if isinstance(count, int) and not isinstance(count, bool):
      return [(evaluator.for_instance(count=i), f'[{i}]') for i in range(count)]
  elif 'for_each' in arguments:
    for_each = evaluator.value(arguments['for_each'])
    if isinstance(for_each, dict):
      items = list(for_each.items())
    elif isinstance(for_each, list):
      items = [(k, k) for k in for_each]
    else:
      items = None
    if items is not None:
      return [(evaluator.for_instance(each=(k, v)), f'["{k}"]') for k, v in items]
  return [(evaluator, '')]


def load_from_path(path):
  """Load and evaluate all ``*.tf`` files in ``path`` (non-recursive)."""
  if not os.path.isdir(path):
    raise ParseError(f'{path} is not a directory')

  source, blocks = _parse_files(path)
  parsed = [(filename, b, *_block_parts(b)) for filename, b in blocks]

  variables, locals_, resources, modules = {}, {}, {}, {}
  evaluator = _Evaluator(source, variables, locals_, resources, modules)
  for _, _, kind, labels, arguments in parsed:
    if kind == 'locals':
      locals_.update(arguments)
    elif kind == 'resource' and len(labels) >= 2:
      resources[(labels[0], labels[1])] = arguments
    elif kind == 'module' and labels:
      modules[labels[0]] = arguments
  for _, _, kind, labels, arguments in parsed:
    if kind == 'variable' and labels and 'default' in arguments:
      variables[labels[0]] = evaluator.value(arguments['default'])

  result = {'module': []}
  for filename, block, kind, labels, arguments in parsed:
    if kind == 'module' and labels:
      key, label, path_ = 'module', labels[0], f'module.{labels[0]}'
    elif kind == 'resource' and len(labels) >= 2:
      key, label, path_ = labels[0], labels[0], f'{labels[0]}.{labels[1]}'
    else:
      continue

    meta = {
      'filename': filename,
      'label': label,
      'line_start': block.meta.line,
      'line_end': block.meta.end_line,
      'path': path_,
    }
    if kind == 'resource':
      meta['type'] = 'resource'

    for instance, suffix in _instances(evaluator, arguments):
      item = {
        '__tfmeta': {
          **meta,
          'label': label + suffix if kind == 'module' else label,
          'path': path_ + suffix,
        }
      }
      for name, node in arguments.items():
        item[name] = instance.attribute_json(filename, node)
      result.setdefault(key, []).append(item)
  return result
