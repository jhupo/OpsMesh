# Python Design Patterns Reference

Distilled from python-patterns.guide. The key insight throughout: many Gang of Four patterns
exist to work around limitations in static languages (no first-class functions, no first-class
classes). Python has those features, so the Pythonic version of most patterns is dramatically
simpler than the textbook version.

## Table of Contents
1. [Composition Over Inheritance](#composition-over-inheritance)
2. [Module Globals Pattern](#module-globals-pattern)
3. [Prebound Methods Pattern](#prebound-methods-pattern)
4. [Sentinel Object Pattern](#sentinel-object-pattern)
5. [Creational Patterns](#creational-patterns)
6. [Structural Patterns](#structural-patterns)
7. [Iterator Pattern](#iterator-pattern)

---

## Composition Over Inheritance

**The most important principle.** Favor composing objects at runtime over inheriting behavior
at class-definition time.

### The Problem: Subclass Explosion
When a class needs to vary along multiple axes (e.g., *where* to log AND *how* to filter),
inheritance creates m × n classes for m destinations and n filters. Each new axis multiplies
the total class count.

### Solution Strategies (from simplest to most structured):

**1. Adapter Pattern** — Wrap one interface to look like another. Python's duck typing makes
this lightweight: an adapter only needs to implement the methods actually called, not the
entire interface.
```python
# Instead of SocketLogger(Logger), wrap a socket to look like a file:
class FileLikeSocket:
    def __init__(self, sock):
        self.sock = sock
    def write(self, data):
        self.sock.sendall(data.encode('ascii'))
    def flush(self):
        pass

logger = Logger(FileLikeSocket(sock))  # Compose at runtime
```

**2. Bridge Pattern** — Split "abstraction" (what callers see) from "implementation" (how
output happens). Both can vary independently.
```python
class Logger:
    def __init__(self, handler):
        self.handler = handler
    def log(self, message):
        self.handler.emit(message)

class FileHandler:
    def __init__(self, file):
        self.file = file
    def emit(self, message):
        self.file.write(message + '\n')
```

**3. Decorator Pattern** — Give wrappers and wrapped objects the same interface so they can
stack. This is NOT Python's `@decorator` syntax — it's the GoF pattern where objects wrap
other objects.
```python
class LogFilter:
    def __init__(self, pattern, logger):
        self.pattern = pattern
        self.logger = logger
    def log(self, message):
        if self.pattern in message:
            self.logger.log(message)

# Stack freely:
log = LogFilter('Error', LogFilter('Critical', FileLogger(sys.stdout)))
```

### When Reviewing Code, Watch For:
- Deep inheritance trees (3+ levels) — almost always should be composition
- Classes that override many parent methods — they want a different thing, not a subclass
- "Mixin" classes that are really just shared utility — extract to standalone functions
- Parallel inheritance hierarchies — a sign that two axes of variation need composition

---

## Module Globals Pattern

Module-level names are Python's way of providing constants, pre-built objects, and
convenient access points.

### Good Uses:
- **Constants**: Immutable values assigned at module level (`MAX_RETRIES = 3`)
- **Computed constants**: Values expensive to create, computed once at import time
  (`INFINITY = float('inf')`)
- **Frozen data structures**: Tuples and frozensets of reference data

### Dangerous Uses:
- **Mutable global state**: Module-level dicts or lists that get modified at runtime create
  hidden coupling between distant code. Distant functions sharing a mutable global are
  essentially communicating through a side channel.
- **Import-time I/O**: Opening files, database connections, or network sockets at import
  time causes surprising side effects and makes testing hard.

### When Reviewing Code, Watch For:
- Module-level mutable containers (lists, dicts, sets) that get modified — flag these
- Module-level code that performs I/O — this runs at import time
- Constants buried deep in functions that should be hoisted to module level for readability

---

## Prebound Methods Pattern

A module instantiates an object privately and exposes its bound methods as module-level
callables. The `random` module is the canonical example: `random.randint()` is actually a
bound method of a hidden `Random` instance.

```python
_instance = Random8()
random = _instance.random
set_seed = _instance.set_seed
```

### When This Pattern Fits:
- The object requires expensive initialization (system calls, entropy)
- Shared state between the methods is actually *beneficial*
- Most callers want the convenience of module-level functions

### When Reviewing Code, Watch For:
- Module-level singletons with no good reason — just use a class
- Objects instantiated at import time that perform I/O — defer construction

---

## Sentinel Object Pattern

Use `None` as the default sentinel. When `None` is a legitimate value, create a private
sentinel:

```python
_MISSING = object()

def get(key, default=_MISSING):
    ...
    if default is _MISSING:
        raise KeyError(key)
    return default
```

### When Reviewing Code, Watch For:
- Functions using `None` as both "not provided" and a valid value
- Sentinel values like `-1` or empty string that could be confused with real data
- Overly complex sentinel schemes — usually `None` or a single `object()` suffices

---

## Creational Patterns

### Abstract Factory
**Python verdict: unnecessary.** In Python, classes and functions are first-class objects.
Instead of a factory class, just pass the class itself (or a callable) as an argument:
```python
# Don't do this:
class DecimalFactory:
    @staticmethod
    def build(string):
        return Decimal(string)

# Do this:
json.loads(text, parse_float=Decimal)
```

### Builder
**Python verdict: useful for convenience only.** The pattern is popular in Python for
libraries that hide complex object construction behind simple API calls (matplotlib's pyplot
is a classic example). The GoF's "dueling builders" concept is rarely used.

When reviewing: a Builder is justified when the alternative would force callers to manually
construct a complex hierarchy of objects.

### Factory Method
**Python verdict: poor fit.** Use dependency injection instead — pass the object in rather
than letting the class create it. If the class really must create objects internally, store
the factory as a class attribute or instance attribute (a callable, not a factory class):
```python
class HTTPConnection:
    response_class = HTTPResponse  # Class attribute factory

    def getresponse(self):
        return self.response_class(self.sock)
```

### Prototype
**Python verdict: unnecessary.** Use `functools.partial`, lambdas, or just pass classes
with arguments in tuples. Python's first-class functions eliminate the need for this pattern.

### Singleton
**Python verdict: use module globals instead.** Modules are already singletons (imported
once). If you need a single instance of a class, instantiate it at module level:
```python
_instance = MyService()
```

Don't use `__new__` magic to enforce single instantiation unless you have a very specific
reason.

### When Reviewing Creational Code, Watch For:
- Factory classes where a function or class reference would suffice
- Singleton patterns with `__new__` when a module global would work
- Builder patterns that add complexity without hiding it from callers
- Premature abstraction: creating factories when there's only one concrete implementation

---

## Structural Patterns

### Composite
Give containers and their contents the same interface. In Python, duck typing makes this
natural — no shared base class required:
```python
class File:
    def get_size(self): ...

class Directory:
    def __init__(self):
        self.children = []
    def get_size(self):
        return sum(child.get_size() for child in self.children)
```

### Decorator Pattern (GoF, not Python @decorators)
Wrap an object with another object that has the same interface. Useful when you can't
subclass (e.g., you don't control object creation). In Python, use `__getattr__` for a
dynamic wrapper:
```python
class LoggingWrapper:
    def __init__(self, wrapped, logger):
        self._wrapped = wrapped
        self._logger = logger
    def write(self, data):
        self._logger.info(f"Writing {len(data)} bytes")
        return self._wrapped.write(data)
    def __getattr__(self, name):
        return getattr(self._wrapped, name)
```

### Flyweight
Share immutable objects to save memory. Python does this internally (small integers, interned
strings, `True`/`False`). In your own code, consider flyweights when you have many objects
with shared intrinsic state. Separate intrinsic (shared) from extrinsic (per-instance) state.

### When Reviewing Structural Code, Watch For:
- Objects that could share state but don't (flyweight opportunity)
- Wrapper classes that re-implement every method of the wrapped object — use `__getattr__`
- Composite structures where containers and contents have different APIs unnecessarily

---

## Iterator Pattern

Python's `for` loop IS the iterator pattern. It's built into the language syntax.

- Implement `__iter__` and `__next__` for custom iterables
- Use generators (`yield`) for most custom iteration — they're simpler than iterator classes
- Use generator expressions for simple transformations

### When Reviewing Iterator Code, Watch For:
- Manual index tracking (`for i in range(len(items))`) when `for item in items` works
- Iterator classes that should be generators
- Missing `__iter__` on objects that logically represent collections
- Not using `itertools` for common iteration patterns (chain, groupby, product)
