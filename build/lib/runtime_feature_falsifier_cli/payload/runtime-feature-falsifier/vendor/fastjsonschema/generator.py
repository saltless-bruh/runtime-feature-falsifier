from collections import OrderedDict
from contextlib import contextmanager
from decimal import Decimal
import re

from .exceptions import JsonSchemaValueException, JsonSchemaValuesException, JsonSchemaDefinitionException
from .indent import indent
from .ref_resolver import RefResolver

# Both mean "this subschema did not match": a subschema behind a $ref becomes its own
# function, which reports through JsonSchemaValuesException while errors are collected.
VALIDATION_EXCEPTIONS = '(JsonSchemaValueException, JsonSchemaValuesException)'


def enforce_list(variable):
    if isinstance(variable, list):
        return variable
    return [variable]


# pylint: disable=too-many-instance-attributes,too-many-public-methods
class CodeGenerator:
    """This class generates validation code from a JSON schema."""

    INDENT = 4

    def __init__(self, definition, resolver=None, detailed_exceptions=True, fast_fail=True):
        self._code = []
        self._compile_regexps = {}
        self._custom_formats = {}
        self._detailed_exceptions = detailed_exceptions
        self._fast_fail = fast_fail
        self._extra_imports_lines = ["from decimal import Decimal"]
        self._extra_imports_objects = {"Decimal": Decimal}
        self._variables = {}
        self._scope_stack = []
        self._scope_counter = 0
        self._last_closed_scope = None
        self._indent = 0
        self._indent_last_line = None
        self._variable = None
        self._variable_name = None
        self._root_definition = definition
        self._definition = None
        self._needed_validation_functions = {}
        self._validation_functions_done = set()
        if resolver is None:
            resolver = RefResolver.from_schema(definition, store={})
        self._resolver = resolver
        self._needed_validation_functions[self._resolver.get_uri()] = self._resolver.get_scope_name()
        self._json_keywords_to_function = OrderedDict()

    @property
    def func_code(self):
        self._generate_func_code()
        return '\n'.join(self._code)

    @property
    def global_state(self):
        self._generate_func_code()
        return dict(
            **self._extra_imports_objects,
            REGEX_PATTERNS=self._compile_regexps,
            re=re,
            JsonSchemaValueException=JsonSchemaValueException,
            JsonSchemaValuesException=JsonSchemaValuesException,
        )

    @property
    def global_state_code(self):
        self._generate_func_code()
        if not self._compile_regexps:
            return '\n'.join(self._extra_imports_lines + [
                'from fastjsonschema import JsonSchemaValueException, JsonSchemaValuesException',
                '',
                '',
            ])
        return '\n'.join(self._extra_imports_lines + [
            'import re',
            'from fastjsonschema import JsonSchemaValueException, JsonSchemaValuesException',
            '',
            '',
            'REGEX_PATTERNS = ' + serialize_regexes(self._compile_regexps),
            '',
        ])

    def _generate_func_code(self):
        if not self._code:
            self.generate_func_code()

    def generate_func_code(self):
        self.l('NoneType = type(None)')
        while self._needed_validation_functions:
            uri, name = self._needed_validation_functions.popitem()
            self.generate_validation_function(uri, name)

    def generate_validation_function(self, uri, name):
        self._validation_functions_done.add(uri)
        self.l('')
        with self._resolver.resolving(uri) as definition:
            with self.l('def {}(data, custom_formats={{}}, name_prefix=None):', name):
                if not self._fast_fail:
                    self.l('errors = []')
                self.generate_func_code_block(definition, 'data', 'data', clear_variables=True)
                if not self._fast_fail:
                    self.l('if errors: raise JsonSchemaValuesException(errors)')
                self.l('return data')

    def generate_func_code_block(self, definition, variable, variable_name, clear_variables=False):
        backup = self._definition, self._variable, self._variable_name
        self._definition, self._variable, self._variable_name = definition, variable, variable_name
        if clear_variables:
            backup_variables = self._variables
            self._variables = {}
        count = self._generate_func_code_block(definition)
        self._definition, self._variable, self._variable_name = backup
        if clear_variables:
            self._variables = backup_variables
        return count

    @contextmanager
    def trial_validation(self):
        fast_fail, self._fast_fail = self._fast_fail, True
        try:
            yield
        finally:
            self._fast_fail = fast_fail

    def _generate_func_code_block(self, definition):
        if not isinstance(definition, dict):
            raise JsonSchemaDefinitionException("definition must be an object")
        if '$ref' in definition:
            return self.generate_ref()
        return self.run_generate_functions(definition)

    def run_generate_functions(self, definition):
        count = 0
        for key, func in self._json_keywords_to_function.items():
            if key in definition:
                func()
                count += 1
        return count

    def generate_ref(self):
        with self._resolver.in_scope(self._definition['$ref']):
            name = self._resolver.get_scope_name()
            uri = self._resolver.get_uri()
            if uri not in self._validation_functions_done:
                self._needed_validation_functions[uri] = name
            assert self._variable_name.startswith("data")
            path = self._variable_name[4:]
            name_arg = '(name_prefix or "data") + "{}"'.format(path)
            if '{' in name_arg:
                name_arg = name_arg + '.format(**locals())'
            if self._fast_fail:
                self.l('{}({variable}, custom_formats, {name_arg})', name, name_arg=name_arg)
            else:
                with self.l('try:', optimize=False):
                    self.l('{}({variable}, custom_formats, {name_arg})', name, name_arg=name_arg)
                with self.l('except JsonSchemaValuesException as e:'):
                    self.l('errors.extend(e.errors)')

    @indent
    def l(self, line, *args, **kwds):
        spaces = ' ' * self.INDENT * self._indent
        name = self._variable_name
        if name:
            assert name.startswith('data')
            name = '" + (name_prefix or "data") + "' + name[4:]
            if '{' in name:
                name = name + '".format(**locals()) + "'
        context = dict(
            self._definition if self._definition and self._definition is not True else {},
            variable=self._variable,
            name=name,
            **kwds
        )
        line = line.format(*args, **context)
        line = line.replace('\n', '\\n').replace('\r', '\\r')
        self._code.append(spaces + line)
        return line

    def e(self, string):
        if isinstance(string, str):
            return string.encode('unicode_escape').decode('ascii').replace('"', '\\"')
        return str(string).replace('"', '\\"')

    def exc(self, msg, *args, append_to_msg=None, rule=None):
        if not self._detailed_exceptions:
            if self._fast_fail:
                self.l('raise JsonSchemaValueException("'+msg+'")', *args)
            else:
                self.l('errors.append(JsonSchemaValueException("'+msg+'"))', *args)
            return
        arg = '"'+msg+'"'
        if append_to_msg:
            arg += ' + (' + append_to_msg + ')'
        msg = (
            'raise JsonSchemaValueException('+arg+', value={variable}, name="{name}", definition={definition}, rule={rule})'
            if self._fast_fail else
            'errors.append(JsonSchemaValueException('+arg+', value={variable}, name="{name}", definition={definition}, rule={rule}))'
        )
        definition = self._expand_refs(self._definition)
        definition_rule = self.e(definition.get(rule) if isinstance(definition, dict) else None)
        self.l(msg, *args, definition=repr_default(definition), rule=repr(rule), definition_rule=definition_rule)

    def _expand_refs(self, definition):
        if isinstance(definition, list):
            return [self._expand_refs(v) for v in definition]
        if not isinstance(definition, dict):
            return definition
        if "$ref" in definition and isinstance(definition["$ref"], str):
            with self._resolver.resolving(definition["$ref"]) as schema:
                return schema
        return {k: self._expand_refs(v) for k, v in definition.items()}

    def _is_variable_in_scope(self, variable_name):
        scope = self._variables.get(variable_name)
        if scope is None:
            return False
        return tuple(self._scope_stack[:len(scope)]) == scope

    def create_variable_with_length(self):
        variable_name = '{}_len'.format(self._variable)
        if self._is_variable_in_scope(variable_name):
            return
        self._variables[variable_name] = tuple(self._scope_stack)
        self.l('{variable}_len = len({variable})')

    def create_variable_keys(self):
        variable_name = '{}_keys'.format(self._variable)
        if self._is_variable_in_scope(variable_name):
            return
        self._variables[variable_name] = tuple(self._scope_stack)
        self.l('{variable}_keys = set({variable}.keys())')

    def create_variable_is_list(self):
        variable_name = '{}_is_list'.format(self._variable)
        if self._is_variable_in_scope(variable_name):
            return
        self._variables[variable_name] = tuple(self._scope_stack)
        self.l('{variable}_is_list = isinstance({variable}, (list, tuple))')

    def create_variable_is_dict(self):
        variable_name = '{}_is_dict'.format(self._variable)
        if self._is_variable_in_scope(variable_name):
            return
        self._variables[variable_name] = tuple(self._scope_stack)
        self.l('{variable}_is_dict = isinstance({variable}, dict)')


def serialize_regexes(patterns_dict):
    regex_patterns = (repr(k) + ": " + repr_regex(v) for k, v in patterns_dict.items())
    return '{\n    ' + ",\n    ".join(regex_patterns) + "\n}"


def repr_default(value):
    if isinstance(value, float) and (value != value or value in (float('inf'), float('-inf'))):
        return "float({!r})".format(str(value))
    if isinstance(value, list):
        return '[' + ', '.join(repr_default(item) for item in value) + ']'
    if isinstance(value, tuple):
        return '(' + ''.join(repr_default(item) + ', ' for item in value) + ')'
    if isinstance(value, dict):
        return '{' + ', '.join(
            '{}: {}'.format(repr_default(k), repr_default(v)) for k, v in value.items()
        ) + '}'
    return repr(value)


def repr_regex(regex):
    all_flags = ("A", "I", "DEBUG", "L", "M", "S", "X")
    flags = " | ".join(f"re.{f}" for f in all_flags if regex.flags & getattr(re, f))
    flags = ", " + flags if flags else ""
    return "re.compile({!r}{})".format(regex.pattern, flags)
