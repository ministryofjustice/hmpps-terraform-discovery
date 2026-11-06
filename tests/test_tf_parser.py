import threading

import pytest

from tf_parser import ParseError, load_from_path


def parse(tmp_path, *files):
  for i, text in enumerate(files):
    (tmp_path / f'file{i}.tf').write_text(text)
  return load_from_path(str(tmp_path))


def module_args(tmp_path, body, preamble=''):
  parsed = parse(tmp_path, f'{preamble}\nmodule "m" {{\n{body}\n}}\n')
  (module,) = parsed['module']
  return module


# Variables and locals


def test_variable_default(tmp_path):
  module = module_args(tmp_path, 'a = var.x', 'variable "x" {\n  default = "hello"\n}')
  assert module['a'] == 'hello'


def test_variable_without_default_is_unresolved(tmp_path):
  module = module_args(tmp_path, 'a = var.x', 'variable "x" {}')
  assert module['a'] == {'__attribute__': 'var.x', '__name__': 'x'}


def test_chained_locals(tmp_path):
  preamble = (
    'variable "env" {\n  default = "dev"\n}\n'
    'locals {\n  base = "app-${var.env}"\n  name = "${local.base}-db"\n}'
  )
  module = module_args(tmp_path, 'a = local.name', preamble)
  assert module['a'] == 'app-dev-db'


def test_local_attribute_traversal(tmp_path):
  preamble = 'locals {\n  cfg = { tier = "small", tags = ["x"] }\n}'
  module = module_args(tmp_path, 'a = local.cfg.tier\nb = local.cfg', preamble)
  assert module['a'] == 'small'
  assert module['b'] == {'tier': 'small', 'tags': ['x']}


def test_self_referencing_local_does_not_recurse(tmp_path):
  module = module_args(tmp_path, 'a = local.x', 'locals {\n  x = local.x\n}')
  assert module['a'] == {'__attribute__': 'local.x', '__name__': 'x'}


# Literals and collections


def test_literals(tmp_path):
  module = module_args(
    tmp_path, 'i = 3\nf = 1.5\nb = true\nn = null\ns = "x"\nl = [1, "a"]'
  )
  assert (module['i'], module['f'], module['b']) == (3, 1.5, True)
  assert module['n'] is None
  assert module['s'] == 'x'
  assert module['l'] == [1, 'a']


def test_object_keys(tmp_path):
  module = module_args(tmp_path, 'o = { a = 1, "b-c" = 2 }')
  assert module['o'] == {'a': 1, 'b-c': 2}


def test_unknown_values_nested_in_collections_become_none(tmp_path):
  module = module_args(tmp_path, 'l = [var.missing, "x"]\no = { k = var.missing }')
  assert module['l'] == [None, 'x']
  assert module['o'] == {'k': None}


def test_list_index(tmp_path):
  preamble = 'locals {\n  xs = ["a", "b"]\n}'
  module = module_args(tmp_path, 'a = local.xs[1]', preamble)
  assert module['a'] == 'b'


# Templates


def test_template_interpolation(tmp_path):
  module = module_args(
    tmp_path, 'a = "${var.x}-${1}"', 'variable "x" {\n  default = "v"\n}'
  )
  assert module['a'] == 'v-1'


def test_partial_template_returns_source_text(tmp_path):
  module = module_args(tmp_path, 'a = "${var.missing}-suffix"')
  assert module['a'] == '${var.missing}-suffix'


def test_string_escapes(tmp_path):
  module = module_args(tmp_path, r'a = "x\\d\n\"q\" $${lit} %%{y} \u00e9"')
  assert module['a'] == 'x\\d\n"q" ${lit} %{y} \u00e9'


def test_heredoc(tmp_path):
  module = module_args(tmp_path, 'a = <<EOT\nline1\nline2\nEOT')
  assert module['a'] == 'line1\nline2\n'


def test_indented_heredoc(tmp_path):
  module = module_args(tmp_path, 'a = <<-EOT\n    line1\n      line2\n    EOT')
  assert module['a'] == 'line1\n  line2\n'


# Functions


@pytest.mark.parametrize(
  'expression,expected',
  [
    ('join(",", ["a", "b"])', 'a,b'),
    ('format("%s-%d", "a", 1)', 'a-1'),
    ('lower("ABC")', 'abc'),
    ('upper("abc")', 'ABC'),
    ('replace("a-b-c", "-", "_")', 'a_b_c'),
    ('replace("v1.2.3", "/v(\\\\d+).*/", "$1")', '1'),
    ('split(",", "a,b")', ['a', 'b']),
    ('concat(["a"], ["b"])', ['a', 'b']),
    ('merge({ a = 1 }, { b = 2 })', {'a': 1, 'b': 2}),
    ('lookup({ a = 1 }, "b", 2)', 2),
    ('coalesce("", "x")', 'x'),
    ('length([1, 2, 3])', 3),
    ('substr("hello", 1, 3)', 'ell'),
    ('tonumber("42")', 42),
    ('trimspace("  x ")', 'x'),
  ],
)
def test_functions(tmp_path, expression, expected):
  assert module_args(tmp_path, f'a = {expression}')['a'] == expected


def test_unknown_function_returns_source_text(tmp_path):
  module = module_args(tmp_path, 'a = sha256("x")')
  assert module['a'] == '${sha256("x")}'


def test_function_with_unresolved_argument_returns_source_text(tmp_path):
  module = module_args(tmp_path, 'a = lower(var.missing)')
  assert module['a'] == '${lower(var.missing)}'


# Operators and conditionals


@pytest.mark.parametrize(
  'expression,expected',
  [
    ('1 + 2 * 3', 7),
    ('2 * 3 + 1', 7),
    ('10 - 4 - 3', 3),
    ('7 % 4', 3),
    ('1 + 2 == 3', True),
    ('true || false && false', True),
    ('!true', False),
    ('-5', -5),
    ('"a" == "a" ? "yes" : "no"', 'yes'),
    ('1 > 2 ? "yes" : "no"', 'no'),
  ],
)
def test_operators(tmp_path, expression, expected):
  assert module_args(tmp_path, f'a = {expression}')['a'] == expected


def test_conditional_on_variable(tmp_path):
  preamble = 'variable "prod" {\n  default = "true"\n}'
  module = module_args(tmp_path, 'a = var.prod == "true" ? "big" : "small"', preamble)
  assert module['a'] == 'big'


def test_operator_with_unknown_operand_is_none(tmp_path):
  module = module_args(tmp_path, 'a = var.missing == "x" ? 1 : 2')
  assert module['a'] is None


# References


def test_resource_attribute_reference(tmp_path):
  preamble = 'resource "aws_db_parameter_group" "pg" {\n  name = "my-pg"\n}'
  module = module_args(tmp_path, 'a = aws_db_parameter_group.pg.name', preamble)
  assert module['a'] == 'my-pg'


def test_unset_resource_attribute_reference(tmp_path):
  preamble = 'resource "aws_db_parameter_group" "pg" {\n  name = "my-pg"\n}'
  module = module_args(tmp_path, 'a = aws_db_parameter_group.pg.id', preamble)
  assert module['a'] == {
    '__attribute__': 'aws_db_parameter_group.pg.id',
    '__name__': 'pg',
    '__ref__': 'aws_db_parameter_group.pg',
    '__type__': 'aws_db_parameter_group',
  }


def test_data_reference(tmp_path):
  module = module_args(tmp_path, 'a = data.aws_iam_policy.p.arn')
  assert module['a'] == {
    '__attribute__': 'data.aws_iam_policy.p.arn',
    '__name__': 'p',
    '__ref__': 'aws_iam_policy.p',
    '__type__': 'aws_iam_policy',
  }


def test_module_reference_resolves_to_module_argument(tmp_path):
  parsed = parse(
    tmp_path,
    'module "a" {\n  application = "App"\n}\n'
    'module "b" {\n  application = module.a.application\n  x = module.a.out\n}\n',
  )
  b = next(m for m in parsed['module'] if m['__tfmeta']['label'] == 'b')
  assert b['application'] == 'App'
  assert b['x'] == {'__attribute__': 'module.a.out', '__name__': 'a'}


# count / for_each


def test_count(tmp_path):
  parsed = parse(tmp_path, 'module "m" {\n  count = 2\n  i = count.index\n}\n')
  assert [m['__tfmeta']['label'] for m in parsed['module']] == ['m[0]', 'm[1]']
  assert [m['__tfmeta']['path'] for m in parsed['module']] == [
    'module.m[0]',
    'module.m[1]',
  ]
  assert [m['i'] for m in parsed['module']] == [0, 1]


def test_count_zero_produces_no_instances(tmp_path):
  parsed = parse(tmp_path, 'module "m" {\n  count = 0\n}\n')
  assert parsed['module'] == []


def test_for_each_set(tmp_path):
  parsed = parse(
    tmp_path,
    'module "m" {\n'
    '  for_each = toset(["a", "b"])\n'
    '  k = each.key\n'
    '  v = each.value\n'
    '}\n',
  )
  assert [m['__tfmeta']['label'] for m in parsed['module']] == ['m["a"]', 'm["b"]']
  assert [(m['k'], m['v']) for m in parsed['module']] == [('a', 'a'), ('b', 'b')]


def test_for_each_map(tmp_path):
  parsed = parse(
    tmp_path,
    'module "m" {\n'
    '  for_each = { x = { size = 1 }, y = { size = 2 } }\n'
    '  size = each.value.size\n'
    '}\n',
  )
  assert [(m['__tfmeta']['path'], m['size']) for m in parsed['module']] == [
    ('module.m["x"]', 1),
    ('module.m["y"]', 2),
  ]


def test_unresolvable_count_produces_single_instance(tmp_path):
  parsed = parse(tmp_path, 'module "m" {\n  count = var.missing\n}\n')
  assert [m['__tfmeta']['label'] for m in parsed['module']] == ['m']


# Output structure


def test_tfmeta(tmp_path):
  parsed = parse(
    tmp_path,
    '# comment\n\nmodule "m" {\n  source = "x"\n}\n',
    'resource "pingdom_check" "c" {\n  name = "n"\n}\n',
  )
  assert parsed['module'][0]['__tfmeta'] == {
    'filename': 'file0.tf',
    'label': 'm',
    'line_start': 3,
    'line_end': 5,
    'path': 'module.m',
  }
  assert parsed['pingdom_check'][0]['__tfmeta'] == {
    'filename': 'file1.tf',
    'label': 'pingdom_check',
    'line_start': 1,
    'line_end': 3,
    'path': 'pingdom_check.c',
    'type': 'resource',
  }


def test_module_key_always_present(tmp_path):
  assert parse(tmp_path, 'variable "x" {}\n') == {'module': []}


def test_empty_directory(tmp_path):
  assert load_from_path(str(tmp_path)) == {'module': []}


def test_references_across_files(tmp_path):
  parsed = parse(
    tmp_path,
    'module "m" {\n  a = var.x\n}\n',
    'variable "x" {\n  default = "y"\n}\n',
  )
  assert parsed['module'][0]['a'] == 'y'


def test_ignores_non_tf_files_and_subdirectories(tmp_path):
  (tmp_path / 'notes.md').write_text('not hcl {')
  (tmp_path / 'vars.tfvars').write_text('x = 1\n')
  (tmp_path / 'sub').mkdir()
  (tmp_path / 'sub' / 'nested.tf').write_text('module "nested" {}\n')
  assert parse(tmp_path, 'module "m" {}\n')['module'][0]['__tfmeta']['label'] == 'm'
  assert len(load_from_path(str(tmp_path))['module']) == 1


def test_trailing_comment_without_newline(tmp_path):
  parsed = parse(tmp_path, 'module "m" {\n  a = 1\n}\n# trailing comment')
  assert parsed['module'][0]['a'] == 1


def test_ignored_block_types(tmp_path):
  parsed = parse(
    tmp_path,
    'terraform {\n  required_version = ">= 1"\n}\n'
    'provider "aws" {\n  region = "eu-west-2"\n}\n'
    'data "aws_caller_identity" "c" {}\n'
    'output "o" {\n  value = 1\n}\n',
  )
  assert parsed == {'module': []}


def test_invalid_hcl_raises_parse_error(tmp_path):
  with pytest.raises(ParseError, match='file0.tf'):
    parse(tmp_path, 'module "m" {\n  a = \n}\n')


def test_missing_directory_raises_parse_error(tmp_path):
  with pytest.raises(ParseError):
    load_from_path(str(tmp_path / 'missing'))


def test_thread_safety(tmp_path):
  parse(
    tmp_path,
    'variable "x" {\n  default = "v"\n}\n'
    'module "m" {\n  count = 3\n  a = "${var.x}-${count.index}"\n}\n',
  )
  expected = load_from_path(str(tmp_path))
  results, errors = [], []

  def worker():
    try:
      for _ in range(20):
        results.append(load_from_path(str(tmp_path)))
    except Exception as e:  # pragma: no cover
      errors.append(e)

  threads = [threading.Thread(target=worker) for _ in range(8)]
  for t in threads:
    t.start()
  for t in threads:
    t.join()
  assert not errors
  assert all(r == expected for r in results)
