import argparse
import os
import sys

from collections import namedtuple, OrderedDict

from clang.cindex import Index, Cursor, TypeKind, CursorKind, Type


def dump(node, depth=1):
    for name in dir(node):
        try:
            if not name.startswith('_') and name not in ('canonical',):
                attr = getattr(node, name)
                if isinstance(attr, (Cursor, Type)):
                    print("    " * depth + "%s:" % name)
                    dump(attr, depth + 1)
                else:
                    print("    " * depth + "%s = %s" % (name, attr))
                    if callable(attr):
                        try:
                            print("    " * (depth + 1) + "-> ", attr())
                        except:
                            print("    " * (depth + 1) + "-> CALL ERROR")
        except Exception as e:
            print("    " * depth + "%s = *%s*" % (name, e))


class Declaration:
    def __init__(self, parent=None, name=None):
        self.parent = parent
        self.name = name

        if self.name and self.parent:
            self.parent.names[self.name] = self


class Context(Declaration):
    def __init__(self, parent=None, name=None):
        super().__init__(parent=parent, name=name)
        self.names = OrderedDict()

    def __getitem__(self, name):
        try:
            return self.names[name]
        except KeyError:
            if self.parent:
                return self.parent.__getitem__(name)
            else:
                raise


class Module(Context):
    def __init__(self, name, parent=None):
        # A module name isn't accessible like a variable,
        # so don't pass it upstream to the parent.
        super().__init__(parent=parent)
        self.name = name
        self.declarations = OrderedDict()
        self.imports = set()
        self.submodules = {}

    @property
    def full_name(self):
        if self.parent:
            return '.'.join([self.parent.full_name, self.name])
        return self.name

    def add_to_context(self, context):
        context.add_submodule(self)

    def add_declaration(self, decl):
        self.declarations[decl.name] = decl
        decl.add_imports(self)

    def add_import(self, module):
        self.imports.add(module)

    def add_imports(self, module):
        pass

    def add_submodule(self, module):
        self.submodules[module.name, module]

    def output(self, out):
        if self.imports:
            for statement in sorted(self.imports):
                out.write(statement)
                out.clear_line()
            out.clear_block()

        for name, decl in self.declarations.items():
            decl.output(out)

        for name, mod in self.submodules.items():
            mod.output(out)


###########################################################################
# Enumerated types
###########################################################################

class Enumeration(Context):
    def __init__(self, parent, name):
        super().__init__(parent=parent, name=name)
        self.enumerators = []

    def add_enumerator(self, entry):
        self.enumerators.append(entry)

    def add_to_context(self, context):
        context.add_declaration(self)

    def add_imports(self, module):
        module.add_import('from enum import Enum')

    def output(self, out, depth=0):
        out.write('    ' * depth + "class %s(Enum):\n" % self.name)
        if self.enumerators:
            for enumerator in self.enumerators:
                out.write('    ' * (depth + 1) + "%s = %s" % (
                    enumerator.key, enumerator.value
                ))
                out.clear_line()
        else:
            out.write('    pass')
            out.clear_line()
        out.clear_block()


EnumValue = namedtuple('EnumValue', ['key', 'value'])


###########################################################################
# Functions
###########################################################################

class Function(Context):
    def __init__(self, parent, name):
        super().__init__(parent=parent, name=name)
        self.parameters = []
        self.statements = []

    def add_parameter(self, parameter):
        self.parameters.append(parameter)

    def add_to_context(self, context):
        context.add_declaration(self)

    def add_imports(self, module):
        pass

    def add_statement(self, statement):
        self.statements.append(statement)
        statement.add_imports(self)

    def output(self, out, depth=0):
        parameters = ', '.join(p.name for p in self.parameters)
        out.write('    ' * depth + "def %s(%s):\n" % (self.name, parameters))
        if self.statements:
            for statement in self.statements:
                out.write('    ' * (depth + 1))
                statement.output(out)
                out.clear_line()
        else:
            out.write('    pass')
        out.clear_block()


class Parameter(Declaration):
    def __init__(self, function, name, ctype, default):
        super().__init__(parent=function, name=name)
        self.ctype = ctype
        self.default = default

    def add_to_context(self, context):
        context.add_parameter(self)


class Variable(Declaration):
    def __init__(self, parent, name, value=None):
        super().__init__(parent=parent, name=name)
        self.value = value

    def add_to_context(self, context):
        context.add_declaration(self)

    def add_imports(self, module):
        pass

    def output(self, out, depth=0):
        out.write('%s = ' % self.name)
        if self.value:
            self.value.output(out)
        else:
            out.write('None')
        out.clear_line()


###########################################################################
# Structs
###########################################################################

class Struct(Context):
    def __init__(self, parent, name):
        super().__init__(parent=parent, name=name)
        self.attributes = OrderedDict()

    def add_imports(self, module):
        pass

    def add_attribute(self, attr):
        self.attributes[attr.name] = attr

    def add_to_context(self, context):
        context.add_declaration(self)

    def output(self, out, depth=0):
        out.write('    ' * depth + "class %s:\n" % self.name)
        if self.attributes:
            out.write('    ' * (depth + 1) + 'def __init__(self):')
            for name, value in self.attributes.items():
                out.write('    ' * (depth + 2) + '%s = ' % name)
                if value:
                    value.output(out)
                else:
                    out.write('None')
        else:
            out.write('    ' * (depth + 1) + 'pass')
        out.clear_block()


###########################################################################
# Classes
###########################################################################

class Class(Context):
    def __init__(self, parent, name):
        super().__init__(parent=parent, name=name)
        self.superclass = None
        self.constructor = None
        self.destructor = None
        self.attributes = OrderedDict()
        self.methods = OrderedDict()
        self.classes = OrderedDict()

    def add_imports(self, module):
        if self.superclass:
            pass

    def add_declaration(self, klass):
        self.classes.append(klass)

    def add_constructor(self, method):
        if self.constructor:
            if self.constructor.statements is None:
                self.constructor = method
            else:
                raise Exception("Cannot handle multiple constructors")
        else:
            self.constructor = method

    def add_destructor(self, method):
        if self.destructor:
            if self.destructor.statements is None:
                self.destructor = method
            else:
                raise Exception("Cannot handle multiple destructors")
        else:
            self.destructor = method

    def add_attribute(self, attr):
        self.attributes[attr.name] = attr

    def add_method(self, method):
        self.methods[method.name] = method

    def add_to_context(self, context):
        context.add_declaration(self)

    def output(self, out, depth=0):
        if self.superclass:
            out.write('    ' * depth + "class %s(%s):\n" % (self.name, self.superclass))
        else:
            out.write('    ' * depth + "class %s:\n" % self.name)
        if self.constructor or self.destructor or self.methods:
            if self.constructor:
                self.constructor.output(out, depth + 1)

            if self.destructor:
                self.destructor.output(out, depth + 1)

            for name, method in self.methods.items():
                method.output(out, depth + 1)
        else:
            out.write('    ' * (depth + 1) + 'pass')
        out.clear_block()


class Constructor(Context):
    def __init__(self, klass):
        super().__init__(parent=klass)
        self.parameters = []
        self.statements = None

    def add_parameter(self, parameter):
        self.parameters.append(parameter)

    def add_to_context(self, klass):
        self.parent.add_constructor(self)

    def add_attribute(self, attr):
        self.parent.add_attribute(attr)

    def add_imports(self, module):
        pass

    def add_statement(self, statement):
        if self.statements:
            self.statements.append(statement)
        else:
            self.statements = [statement]
        statement.add_imports(self)

    def output(self, out, depth=0):
        if self.parameters:
            parameters = ', '.join(
                p.name if p.name else 'arg%s' % (i + 1)
                for i, p in enumerate(self.parameters))
            out.write('    ' * depth + "def __init__(self, %s):\n" % parameters)
        else:
            out.write('    ' * depth + "def __init__(self):\n")
        if self.parent.attributes or self.statements:
            for name, attr in self.parent.attributes.items():
                out.write('    ' * (depth + 1))
                attr.output(out)
                out.clear_line()

            for statement in self.statements:
                out.write('    ' * (depth + 1))
                statement.output(out)
                out.clear_line()
        else:
            out.write('    ' * (depth + 1) + 'pass')
        out.clear_block(blank_lines=1)


class Destructor(Context):
    def __init__(self, klass):
        super().__init__(parent=klass)
        self.parameters = []
        self.statements = None

    def add_to_context(self, klass):
        self.parent.add_destructor(self)

    def add_imports(self, module):
        pass

    def add_statement(self, statement):
        if self.statements:
            self.statements.append(statement)
        else:
            self.statements = [statement]
        statement.add_imports(self)

    def output(self, out, depth=0):
        out.write('    ' * depth + "def __del__(self):\n")
        if self.statements:
            for statement in self.statements:
                out.write('    ' * (depth + 1))
                statement.output(out)
                out.clear_line()
        else:
            out.write('    ' * (depth + 1) + 'pass')
        out.clear_block(blank_lines=1)


# An instance method on a class.
class Method(Context):
    def __init__(self, klass, name, pure_virtual):
        super().__init__(parent=klass, name=name)
        self.parameters = []
        self.statements = None
        self.pure_virtual = pure_virtual

    def add_parameter(self, parameter):
        self.parameters.append(parameter)

    def add_to_context(self, context):
        self.parent.add_method(self)

    def add_imports(self, module):
        pass

    def add_statement(self, statement):
        if self.statements:
            self.statements.append(statement)
        else:
            self.statements = [statement]
        statement.add_imports(self)

    def output(self, out, depth=0):
        if self.parameters:
            parameters = ', '.join(p.name for p in self.parameters)
            out.write('    ' * depth + "def %s(self, %s):\n" % (self.name, parameters))
        else:
            out.write('    ' * depth + "def %s(self):\n" % self.name)
        if self.statements:
            for statement in self.statements:
                out.write('    ' * (depth + 1))
                statement.output(out)
                out.clear_line()
        elif self.pure_virtual:
            out.write('    ' * (depth + 1) + 'raise NotImplementedError()')
        else:
            out.write('    ' * (depth + 1) + 'pass')
        out.clear_block(blank_lines=1)


# An attribute declaration
class Attribute(Declaration):
    def __init__(self, klass, name, value=None):
        super().__init__(parent=klass, name=name)
        self.value = value

    def add_to_context(self, context):
        context.add_attribute(self)

    def add_imports(self, module):
        pass

    def output(self, out):
        out.write('self.%s = ' % self.name)
        if self.value:
            self.value.output(out)
        else:
            out.write("None")
        out.clear_line()


###########################################################################
# Statements
###########################################################################

class Return:
    def __init__(self):
        self.value = None

    def add_imports(self, module):
        pass

    def add_expression(self, expr):
        self.value = expr

    def output(self, out, depth=0):
        out.write('return')
        if self.value:
            out.write(' ')
            self.value.output(out)
            out.clear_line()


###########################################################################
# Expressions
###########################################################################

# A reference to a variable
class Reference:
    def __init__(self, ref):
        self.ref = ref

    def add_imports(self, module):
        pass

    def output(self, out):
        out.write(self.ref)


# A reference to self.
class SelfReference:
    def add_imports(self, module):
        pass

    def output(self, out):
        out.write('self')


# A reference to an attribute on a class
class AttributeReference:
    def __init__(self, instance, attr):
        self.instance = instance
        self.attr = attr

    # def add_to_context(self, context):
    #     pass

    def add_imports(self, module):
        pass

    def output(self, out):
        self.instance.output(out)
        out.write('.%s' % self.attr)


class Literal:
    def __init__(self, value):
        self.value = value

    def add_imports(self, module):
        pass

    def output(self, out):
        out.write(str(self.value))


class UnaryOperation:
    def add_imports(self, module):
        pass

    def output(self, out):
        self.lvalue.output(out)
        out.write(' %s ' % self.op)
        self.rvalue.output(out)


class BinaryOperation:
    def add_imports(self, module):
        pass

    def output(self, out):
        self.lvalue.output(out)
        out.write(' %s ' % self.op)
        self.rvalue.output(out)


class ConditionalOperation:
    def add_imports(self, module):
        pass

    def output(self, out):
        out.write('(')
        self.true_result.output(out)
        out.write(' if ')
        self.condition.output(out)
        out.write(' else ')
        self.false_result.output(out)
        out.write(')')


class FunctionCall:
    def __init__(self, fn):
        self.fn = fn
        self.arguments = []

    def add_argument(self, argument):
        self.arguments.append(argument)

    def add_imports(self, module):
        pass

    def output(self, out):
        self.fn.output(out)
        out.write('(')
        if self.arguments:
            self.arguments[0].output(out)
            for arg in self.arguments[1:]:
                out.write(', ')
                arg.output(out)
        out.write(')')


class New:
    def __init__(self, klass, function_call):
        self.klass = klass
        self.arguments = function_call.arguments

    def add_imports(self, module):
        pass

    def output(self, out):
        out.write('%s(' % self.name)
        if self.arguments:
            self.arguments[0].output(out)
            for arg in self.arguments[1:]:
                out.write(', ')
                arg.output(out)
        out.write(')')


###########################################################################
# Code generator
###########################################################################

class CodeWriter:
    def __init__(self, out):
        self.out = out
        self.line_cleared = True
        self.block_cleared = 2

    def write(self, content):
        self.out.write(content)
        self.line_cleared = False
        self.block_cleared = 0

    def clear_line(self):
        if not self.line_cleared:
            self.out.write('\n')
            self.line_cleared = True

    def clear_block(self, blank_lines=2):
        self.clear_line()
        while self.block_cleared < blank_lines:
            self.out.write('\n')
            self.block_cleared += 1


class BaseGenerator:
    def __init__(self):
        self.index = Index.create(excludeDecls=True)

    def diagnostics(self, out):
        for diag in self.tu.diagnostics:
            print('%s %s (line %s, col %s) %s' % (
                    {
                        4: 'FATAL',
                        3: 'ERROR',
                        2: 'WARNING',
                        1: 'NOTE',
                        0: 'IGNORED',
                    }[diag.severity],
                    diag.location.file,
                    diag.location.line,
                    diag.location.column,
                    diag.spelling
                ), file=out)


class Generator(BaseGenerator):
    def __init__(self, name):
        super().__init__()
        self.module = Module(name)
        self.filenames = set()

    def output(self, out):
        self.module.output(CodeWriter(out))

    def parse(self, filename):
        self.filenames.add(os.path.abspath(filename))
        self.tu = self.index.parse(None, [filename])
        self.handle(self.tu.cursor, self.module)

    def parse_text(self, filename, content):
        self.filenames.add(os.path.abspath(filename))
        self.tu = self.index.parse(filename, unsaved_files=[(filename, content)])
        self.handle(self.tu.cursor, self.module)

    def handle(self, node, context=None):
        if (os.path.abspath(node.spelling) in self.filenames
                or (node.location.file.name
                    and os.path.abspath(node.location.file.name) in self.filenames)):
            try:
                # print(node.kind, node.spelling, node.location.file)
                handler = getattr(self, 'handle_%s' % node.kind.name.lower())
            except AttributeError:
                print("Ignoring node of type %s" % node.kind, file=sys.stderr)
                handler = None
        else:
            print("Ignoring node in file %s" % node.location.file, file=sys.stderr)
            handler = None

        if handler:
            return handler(node, context)

    def handle_unexposed_decl(self, node, context):
        # Ignore unexposed declarations (e.g., friend qualifiers)
        pass

    def handle_struct_decl(self, node, context):
        struct = Struct(context, node.spelling)
        for child in node.get_children():
            decl = self.handle(child, struct)
            if decl:
                decl.add_to_context(struct)
        return struct

    # def handle_union_decl(self, node, context):

    def handle_class_decl(self, node, context):
        klass = Class(context, node.spelling)
        for child in node.get_children():
            decl = self.handle(child, klass)
            if decl:
                decl.add_to_context(klass)
        return klass

    def handle_enum_decl(self, node, context):
        enum = Enumeration(context, node.spelling)
        for child in node.get_children():
            enum.add_enumerator(self.handle(child, enum))
        return enum

    def handle_field_decl(self, node, context):
        try:
            value = self.handle(next(node.get_children()), context)
            return Attribute(context, node.spelling, value)
        except StopIteration:
            return None
            # Alternatively; explicitly set the attribute to None.
            # return Attribute(context, node.spelling)

    def handle_enum_constant_decl(self, node, enum):
        return EnumValue(node.spelling, node.enum_value)

    def handle_function_decl(self, node, context):
        function = Function(context, node.spelling)

        children = node.get_children()
        # If the return type is RECORD, then the first
        # child will be a TYPE_REF for that class; skip it
        if node.result_type.kind in (TypeKind.RECORD, TypeKind.LVALUEREFERENCE, TypeKind.POINTER):
            next(children)

        for child in children:
            decl = self.handle(child, function)
            if decl:
                decl.add_to_context(function)
        return function

    def handle_var_decl(self, node, context):
        try:
            children = node.get_children()
            # If this is a node of type RECORD, then the
            # first node will be a type declaration; we
            # can ignore that node.
            if node.type.kind == TypeKind.RECORD:
                next(children)
            value = self.handle(next(children), context)
            return Variable(context, node.spelling, value)
        except:
            return None
            # Alternatively; explicitly set to None
            # return Variable(context, node.spelling)

    def handle_parm_decl(self, node, function):
        # FIXME: need to pay attention to parameter declarations
        # that include an assignment.
        return Parameter(function, node.spelling, None, None)

    def handle_typedef_decl(self, node, context):
        # Typedefs aren't needed, so ignore them
        pass

    def handle_cxx_method(self, node, context):
        # If this is an inline method, the context will be the
        # enclosing class, and the inline declaration will double as the
        # prototype.
        #
        # If it isn't inline, the context will be a module, and the
        # prototype will be separate. In this case, the method will
        # be found  twice - once as the prototype, and once as the
        # definition.  Parameters are handled as part of the prototype;
        # this handle method only returns a new node when it finds the
        # prototype. When the body method is encountered, it finds the
        # prototype method (which will be the TYPE_REF in the first
        # child node), and adds the body definition.
        if isinstance(context, Class):
            method = Method(context, node.spelling, node.is_pure_virtual_method())
            is_prototype = True
        else:
            method = None
            is_prototype = False

        children = node.get_children()

        # If the return type is RECORD, then the first child will be a
        # TYPE_REF describing the return type; that node can be skipped.
        if node.result_type.kind == TypeKind.RECORD:
            next(children)

        for child in children:
            decl = self.handle(child, method)
            if method is None:
                # First node will be a TypeRef for the class.
                # Use this to get the method.
                method = context[decl.ref].methods[node.spelling]
            elif decl:
                if is_prototype or child.kind != CursorKind.PARM_DECL:
                    decl.add_to_context(method)

        # Only add a new node for the prototype.
        if is_prototype:
            return method

    def handle_namespace(self, node, module):
        for child in node.get_children():
            decl = self.handle(child, module)
            if decl:
                decl.add_to_context(module)

    # def handle_linkage_spec(self, node, context):
    def handle_constructor(self, node, context):
        # If this is an inline constructor, the context will be the
        # enclosing class, and the inline declaration will double as the
        # prototype.
        #
        # If it isn't inline, the context will be a module, and the
        # prototype will be separate. In this case, the constructor will
        # be found  twice - once as the prototype, and once as the
        # definition.  Parameters are handled as part of the prototype;
        # this handle method only returns a new node when it finds the
        # prototype. When the body method is encountered, it finds the
        # prototype constructor (which will be the TYPE_REF in the first
        # child node), and adds the body definition.
        if isinstance(context, Class):
            constructor = Constructor(context)
            is_prototype = True
        else:
            constructor = None
            is_prototype = False

        for child in node.get_children():
            decl = self.handle(child, constructor)
            if constructor is None:
                # First node will be a TypeRef for the class.
                # Use this to get the constructor
                constructor = context[decl.ref].constructor
            elif decl:
                if is_prototype or child.kind != CursorKind.PARM_DECL:
                    decl.add_to_context(constructor)

        # Only add a new node for the prototype.
        if is_prototype:
            return constructor

    def handle_destructor(self, node, context):
        # If this is an inline destructor, the context will be the
        # enclosing class, and the inline declaration will double as the
        # prototype.
        #
        # If it isn't inline, the context will be a module, and the
        # prototype will be separate. In this case, the destructor will
        # be found  twice - once as the prototype, and once as the
        # definition.  Parameters are handled as part of the prototype;
        # this handle method only returns a new node when it finds the
        # prototype. When the body method is encountered, it finds the
        # prototype destructor (which will be the TYPE_REF in the first
        # child node), and adds the body definition.

        if isinstance(context, Class):
            destructor = Destructor(context)
            is_prototype = True
        else:
            destructor = None
            is_prototype = False

        for child in node.get_children():
            decl = self.handle(child, destructor)
            if destructor is None:
                # First node will be a TypeRef for the class.
                # Use this to get the destructor
                destructor = context[decl.ref].destructor
            elif decl:
                if is_prototype or child.kind != CursorKind.PARM_DECL:
                    decl.add_to_context(destructor)

        # Only add a new node for the prototype.
        if is_prototype:
            return destructor

    # def handle_conversion_function(self, node, context):
    # def handle_template_type_parameter(self, node, context):
    # def handle_template_non_type_parameter(self, node, context):
    # def handle_template_template_parameter(self, node, context):
    # def handle_function_template(self, node, context):
    # def handle_class_template(self, node, context):
    # def handle_class_template_partial_specialization(self, node, context):
    # def handle_namespace_alias(self, node, context):
    # def handle_using_directive(self, node, context):
    # def handle_using_declaration(self, node, context):
    # def handle_type_alias_decl(self, node, context):

    def handle_cxx_access_spec_decl(self, node, context):
        # Ignore access specifiers; everything is public.
        pass

    def handle_type_ref(self, node, context):
        return Reference(node.spelling.split()[1])

    def handle_cxx_base_specifier(self, node, context):
        context.superclass = node.spelling.split(' ')[1]

    # def handle_template_ref(self, node, context):
    # def handle_namespace_ref(self, node, context):
    def handle_member_ref(self, node, context):
        try:
            child = next(node.get_children())
            ref = AttributeReference(self.handle(child, context), node.spelling)

            try:
                next(children)
                raise Exception("Member reference has multiple children.")
            except StopIteration:
                pass
        except StopIteration:
            # An implicit reference to `this`
            ref = AttributeReference(SelfReference(), node.spelling)

        return ref

    # def handle_label_ref(self, node, context):
    # def handle_overloaded_decl_ref(self, node, context):
    # def handle_variable_ref(self, node, context):
    # def handle_invalid_file(self, node, context):
    # def handle_no_decl_found(self, node, context):
    # def handle_not_implemented(self, node, context):
    # def handle_invalid_code(self, node, context):

    def handle_unexposed_expr(self, node, statement):
        # Ignore unexposed nodes; pass whatever is the first
        # (and should be only) child unaltered.
        children = node.get_children()
        first_child = next(children)
        try:
            next(children)
            raise Exception("Unexposed expression has multiple children.")
        except StopIteration:
            pass

        return self.handle(first_child, statement)

    def handle_decl_ref_expr(self, node, statement):
        return Reference(node.spelling)

    def handle_member_ref_expr(self, node, context):
        children = node.get_children()
        try:
            first_child = next(children)
            ref = AttributeReference(self.handle(first_child, context), node.spelling)

            try:
                next(children)
                raise Exception("Member reference expression has multiple children.")
            except StopIteration:
                pass
        except StopIteration:
            # An implicit reference to `this`
            ref = AttributeReference(SelfReference(), node.spelling)

        return ref

    def handle_call_expr(self, node, context):
        children = node.get_children()
        if node.type.kind == TypeKind.RECORD:
            fn = self.handle(next(children), context)
        else:
            fn = FunctionCall(self.handle(next(children), context))

            for child in children:
                fn.add_argument(self.handle(child, context))

        # print("   FN", fn)
        return fn

    # def handle_block_expr(self, node, context):

    def handle_integer_literal(self, node, context):
        return Literal(int(next(node.get_tokens()).spelling))

    def handle_floating_literal(self, node, context):
        return Literal(float(next(node.get_tokens()).spelling))

    # def handle_imaginary_literal(self, node, context):

    def handle_string_literal(self, node, context):
        return Literal(next(node.get_tokens()).spelling)

    def handle_character_literal(self, node, context):
        return Literal(next(node.get_tokens()).spelling)

    def handle_paren_expr(self, node, context):
        try:
            children = node.get_children()
            parens = Parentheses(self.handle(next(children), context))
        except StopIteration:
            raise Exception("Parentheses must contain an expression.")

        try:
            next(children)
            raise Exception("Parentheses can only contain one expression.")
        except StopIteration:
            pass

        return parens

    def handle_unary_operator(self, node, context):
        try:
            unaryop = UnaryOperation()
            children = node.get_children()

            unaryop.op = self.handle(next(children), unaryop)
            unaryop.value = self.handle(next(children), unaryop)
        except StopIteration:
            raise Exception("Unary expression requires 2 child nodes.")

        try:
            next(children)
            raise Exception("Unary expression has > 2 child nodes.")
        except StopIteration:
            pass

        return unaryop

    # def handle_array_subscript_expr(self, node, context):
    def handle_binary_operator(self, node, context):
        try:
            binop = BinaryOperation()
            children = node.get_children()

            lnode = next(children)
            binop.lvalue = self.handle(lnode, binop)
            binop.op = list(lnode.get_tokens())[-1].spelling

            rnode = next(children)
            binop.rvalue = self.handle(rnode, binop)
        except StopIteration:
            raise Exception("Binary expression requires 2 child nodes.")

        try:
            next(children)
            raise Exception("Binary expression has > 2 child nodes.")
        except StopIteration:
            pass

        return binop

    # def handle_compound_assignment_operator(self, node, context):
    def handle_conditional_operator(self, node, context):
        condop = ConditionalOperation()
        children = node.get_children()

        condop.true_value = self.handle(next(children), condop)
        condop.condition = self.handle(next(children), condop)
        condop.false_result = self.handle(next(children), condop)

        return condop

    # def handle_cstyle_cast_expr(self, node, context):
    # def handle_compound_literal_expr(self, node, context):
    # def handle_init_list_expr(self, node, context):
    # def handle_addr_label_expr(self, node, context):
    # def handle_stmtexpr(self, node, context):
    # def handle_generic_selection_expr(self, node, context):
    # def handle_gnu_null_expr(self, node, context):
    # def handle_cxx_static_cast_expr(self, node, context):
    # def handle_cxx_dynamic_cast_expr(self, node, context):
    # def handle_cxx_reinterpret_cast_expr(self, node, context):
    # def handle_cxx_const_cast_expr(self, node, context):
    def handle_cxx_functional_cast_expr(self, node, context):
        try:
            children = node.get_children()

            to_type = self.handle(next(children), context)
            value = self.handle(next(children), context)

            # print("REF:", to_type.ref)
            # print("REF TO:", context, context[to_type.ref])
        except StopIteration:
            raise Exception("Functional cast requires 2 child nodes.")
        except Exception as e:
            print (e)

        try:
            next(children)
            raise Exception("Functional cast has > 2 child nodes.")
        except StopIteration:
            pass

        return value

    # def handle_cxx_typeid_expr(self, node, context):
    # def handle_cxx_bool_literal_expr(self, node, context):
    def handle_cxx_this_expr(self, node, context):
        return SelfReference()

    # def handle_cxx_throw_expr(self, node, context):
    def handle_cxx_new_expr(self, node, context):
        try:
            children = node.get_children()

            klass = self.handle(next(children), context)
            call = self.handle(next(children), context)

            value = New(klass, call)
        except StopIteration:
            raise Exception("new requires 2 child nodes.")

        try:
            raise Exception("new has > 2 child nodes.")
        except StopIteration:
            pass

        return value

    # def handle_cxx_delete_expr(self, node, context):
    # def handle_cxx_unary_expr(self, node, context):
    # def handle_pack_expansion_expr(self, node, context):
    # def handle_size_of_pack_expr(self, node, context):
    # def handle_lambda_expr(self, node, context):
    # def handle_unexposed_stmt(self, node, context):
    # def handle_label_stmt(self, node, context):

    def handle_compound_stmt(self, node, context):
        for child in node.get_children():
            statement = self.handle(child, context)
            if statement:
                context.add_statement(statement)

    # def handle_case_stmt(self, node, context):
    # def handle_default_stmt(self, node, context):
    # def handle_if_stmt(self, node, context):
    # def handle_switch_stmt(self, node, context):
    # def handle_while_stmt(self, node, context):
    # def handle_do_stmt(self, node, context):
    # def handle_for_stmt(self, node, context):
    # def handle_goto_stmt(self, node, context):
    # def handle_indirect_goto_stmt(self, node, context):
    # def handle_continue_stmt(self, node, context):
    # def handle_break_stmt(self, node, context):
    def handle_return_stmt(self, node, context):
        retval = Return()
        try:
            retval.value = self.handle(next(node.get_children()), context)
        except:
            pass

        return retval

    # def handle_asm_stmt(self, node, context):
    # def handle_cxx_catch_stmt(self, node, context):
    # def handle_cxx_try_stmt(self, node, context):
    # def handle_cxx_for_range_stmt(self, node, context):
    # def handle_seh_try_stmt(self, node, context):
    # def handle_seh_except_stmt(self, node, context):
    # def handle_seh_finally_stmt(self, node, context):
    # def handle_ms_asm_stmt(self, node, context):
    # def handle_null_stmt(self, node, context):
    def handle_decl_stmt(self, node, context):
        try:
            return self.handle(next(node.get_children()), context)
        except StopIteration:
            pass
        except:
            raise Exception("Don't know how to handle multiple statements")

    def handle_translation_unit(self, node, tu):
        for child in node.get_children():
            decl = self.handle(child, tu)
            if decl:
                decl.add_to_context(tu)

    # def handle_unexposed_attr(self, node, context):
    # def handle_ib_action_attr(self, node, context):
    # def handle_ib_outlet_attr(self, node, context):
    # def handle_ib_outlet_collection_attr(self, node, context):
    # def handle_cxx_final_attr(self, node, context):
    # def handle_cxx_override_attr(self, node, context):
    # def handle_annotate_attr(self, node, context):
    # def handle_asm_label_attr(self, node, context):
    # def handle_packed_attr(self, node, context):
    # def handle_pure_attr(self, node, context):
    # def handle_const_attr(self, node, context):
    # def handle_noduplicate_attr(self, node, context):
    # def handle_cudaconstant_attr(self, node, context):
    # def handle_cudadevice_attr(self, node, context):
    # def handle_cudaglobal_attr(self, node, context):
    # def handle_cudahost_attr(self, node, context):
    # def handle_cudashared_attr(self, node, context):
    # def handle_visibility_attr(self, node, context):
    # def handle_dllexport_attr(self, node, context):
    # def handle_dllimport_attr(self, node, context):
    # def handle_preprocessing_directive(self, node, context):
    # def handle_macro_definition(self, node, context):
    # def handle_macro_instantiation(self, node, context):
    # def handle_inclusion_directive(self, node, context):
    # def handle_module_import_decl(self, node, context):
    # def handle_type_alias_template_decl(self, node, context):

    ############################################################
    # Objective-C methods
    # If an algorithm exists in Objective C, implementing these
    # methods will allow conversion of that code.
    ############################################################
    # def handle_objc_synthesize_decl(self, node, context):
    # def handle_objc_dynamic_decl(self, node, context):
    # def handle_objc_super_class_ref(self, node, context):
    # def handle_objc_protocol_ref(self, node, context):
    # def handle_objc_class_ref(self, node, context):
    # def handle_objc_message_expr(self, node, context):
    # def handle_objc_string_literal(self, node, context):
    # def handle_objc_encode_expr(self, node, context):
    # def handle_objc_selector_expr(self, node, context):
    # def handle_objc_protocol_expr(self, node, context):
    # def handle_objc_bridge_cast_expr(self, node, context):
    # def handle_obj_bool_literal_expr(self, node, context):
    # def handle_obj_self_expr(self, node, context):
    # def handle_objc_at_try_stmt(self, node, context):
    # def handle_objc_at_catch_stmt(self, node, context):
    # def handle_objc_at_finally_stmt(self, node, context):
    # def handle_objc_at_throw_stmt(self, node, context):
    # def handle_objc_at_synchronized_stmt(self, node, context):
    # def handle_objc_autorelease_pool_stmt(self, node, context):
    # def handle_objc_for_collection_stmt(self, node, context):
    # def handle_objc_interface_decl(self, node, context):
    # def handle_objc_category_decl(self, node, context):
    # def handle_objc_protocol_decl(self, node, context):
    # def handle_objc_property_decl(self, node, context):
    # def handle_objc_ivar_decl(self, node, context):
    # def handle_objc_instance_method_decl(self, node, context):
    # def handle_objc_class_method_decl(self, node, context):
    # def handle_objc_implementation_decl(self, node, context):
    # def handle_objc_category_impl_decl(self, node, context):


# A simpler version of Generator that just
# dumps the tree structure.
class Dumper(BaseGenerator):
    def parse(self, filename):
        self.tu = self.index.parse(None, [filename])
        self.diagnostics(sys.stderr)

        print('===', filename)
        self.handle(self.tu.cursor, 0)

    def handle(self, node, depth=0):
        print(
            '    ' * depth,
            node.kind,
            '(type:%s | result type:%s)' % (node.type.kind, node.result_type.kind),
            node.spelling,
        )

        for child in node.get_children():
            self.handle(child, depth + 1)

if __name__ == '__main__':
    opts = argparse.ArgumentParser(
        description='Display AST structure for C++ file.',
    )

    opts.add_argument(
        'filename',
        metavar='file.cpp',
        help='The file(s) to dump.',
        nargs="+"
    )

    args = opts.parse_args()

    dumper = Dumper()
    for filename in args.filename:
        dumper.parse(filename)
