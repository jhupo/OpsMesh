# Engineering Principles Reference

Distilled from Real Python's SOLID guide, TDD best practices, and the YAGNI/KISS traditions.

## Table of Contents
1. [YAGNI](#yagni)
2. [KISS](#kiss)
3. [SOLID Principles](#solid-principles)
4. [Test-Driven Development](#test-driven-development)
5. [Applying Principles Together](#applying-principles-together)

---

## YAGNI

"You Ain't Gonna Need It" — from Extreme Programming.

**The rule**: Don't add functionality until it is necessary. Don't build abstractions for
hypothetical future requirements.

### Common YAGNI Violations in Python:

**Premature abstraction**: Creating an ABC with one implementation.
```python
# YAGNI violation — there's only one payment processor
class PaymentProcessor(ABC):
    @abstractmethod
    def process(self, amount): ...

class StripeProcessor(PaymentProcessor):
    def process(self, amount): ...

# Better — just write the class directly
class StripeProcessor:
    def process(self, amount): ...
# If you later need a second processor, THEN extract the interface
```

**Speculative generality**: Adding parameters, config options, or extension points "just in
case." Each one adds code to write, test, and maintain.

**Over-engineering data access**: Building a full repository pattern when you have one
database and one way of querying it.

**Feature flags for features that don't exist**: Building toggle infrastructure before
there's anything to toggle.

### How to Apply When Reviewing:
- For each abstraction layer, ask: "Does this have two or more concrete uses RIGHT NOW?"
- For each parameter, ask: "Is anything currently passing a non-default value?"
- For each extension point, ask: "Has anyone actually extended this?"
- If the answer is "no" or "not yet" — the code is speculative and should be simplified.

### The YAGNI Escape Hatch:
YAGNI does NOT mean "never plan ahead." It means:
- DO think about the likely direction of change
- DO structure code so it's easy to modify later
- DON'T build the modification before it's needed
- DO write clean, well-tested code that's easy to refactor

---

## KISS

"Keep It Simple, Stupid" — every layer of abstraction, every clever trick, every design
pattern adds cognitive load. That load must be justified by concrete benefit.

### Complexity Red Flags:
- A class that exists only to hold one method
- Metaclasses used for anything a class decorator could do
- Custom descriptors where a `@property` suffices
- Abstract base classes with one subclass
- `**kwargs` passed through three layers of calls
- Decorators that change function signatures
- Multiple inheritance with complex MRO
- Dynamic attribute creation via `__getattr__` when explicit attributes would be clearer

### The KISS Test:
For any piece of code, ask: "Could a junior developer understand this in under 5 minutes?"
If not, is the complexity *necessary* for correctness, or is it accidental?

### Python-Specific KISS Guidance:
- Prefer functions over classes when there's no state to manage
- Prefer `dataclasses` or `NamedTuple` over hand-rolled `__init__`/`__repr__`/`__eq__`
- Prefer list comprehensions over `map`/`filter` with lambdas
- Prefer `pathlib` over manual string concatenation for paths
- Prefer f-strings over `.format()` or `%` formatting
- Prefer `contextlib.contextmanager` over writing `__enter__`/`__exit__`

---

## SOLID Principles

### Single Responsibility Principle (SRP)

A class should have only one reason to change.

**The test**: Describe what the class does. If you use "and," it might have too many
responsibilities.

**Common Python violations**:
```python
# Violates SRP — handles both file management AND data processing
class DataProcessor:
    def read_file(self, path): ...
    def parse_csv(self, content): ...
    def validate_data(self, records): ...
    def compute_statistics(self, records): ...
    def write_report(self, stats, output_path): ...
```

**Better**:
```python
class CSVParser:
    def parse(self, content): ...

class DataValidator:
    def validate(self, records): ...

class StatisticsCalculator:
    def compute(self, records): ...

class ReportWriter:
    def write(self, stats, output_path): ...
```

**Caution**: Don't over-split. If a class is small and cohesive, having two related methods
is fine. SRP is about "reasons to change," not "number of methods."

### Open/Closed Principle (OCP)

Software entities should be open for extension but closed for modification.

**In Python, this often means**:
- Use polymorphism: define a protocol/interface, add new classes without changing existing ones
- Use composition: inject behavior through constructor parameters
- Use higher-order functions: accept callables that customize behavior

```python
# Closed for modification — adding a new shape doesn't touch existing code
class Shape(Protocol):
    def area(self) -> float: ...

def total_area(shapes: list[Shape]) -> float:
    return sum(s.area() for s in shapes)

# Extension — just add a new class
class Triangle:
    def __init__(self, base, height):
        self.base = base
        self.height = height
    def area(self):
        return 0.5 * self.base * self.height
```

**Watch for**: Long chains of `if/elif` or `isinstance` checks — these are the opposite of
OCP. Each new type requires modifying the chain.

### Liskov Substitution Principle (LSP)

Subtypes must be substitutable for their base types without altering the correctness of
the program.

**The classic Python violation**:
```python
class Rectangle:
    def __init__(self, width, height):
        self.width = width
        self.height = height

class Square(Rectangle):
    def __init__(self, side):
        super().__init__(side, side)

    @Rectangle.width.setter
    def width(self, value):
        self._width = value
        self._height = value  # Surprise! Setting width also changes height
```

**LSP violations to watch for**:
- Subclass methods that raise `NotImplementedError` for inherited methods
- Overridden methods that change the contract (different return types, different side effects)
- Subclasses that require isinstance checks to use correctly
- Methods that silently ignore arguments their parent class would use

**Python-specific note**: Duck typing means LSP applies to any object used in place of
another, not just formal subclasses. If a function expects a file-like object, anything
passed to it must honor the file-like contract.

### Interface Segregation Principle (ISP)

Clients should not be forced to depend on methods they do not use.

**In Python, this means**:
- Prefer small protocols over large ABCs
- Don't force implementors to stub out methods they don't need
- Use `typing.Protocol` for structural subtyping

```python
# Too broad — forces every printer to implement scan and fax
class Machine(ABC):
    @abstractmethod
    def print(self, doc): ...
    @abstractmethod
    def scan(self, doc): ...
    @abstractmethod
    def fax(self, doc): ...

# Better — separate interfaces
class Printer(Protocol):
    def print(self, doc): ...

class Scanner(Protocol):
    def scan(self, doc): ...
```

**Watch for**:
- Classes with methods that raise `NotImplementedError` — the interface is too broad
- ABCs with many abstract methods that most subclasses don't need
- "God interfaces" that try to be everything to everyone

### Dependency Inversion Principle (DIP)

High-level modules should not depend on low-level modules. Both should depend on abstractions.

**In Python, this often looks like**:
```python
# Violates DIP — tightly coupled to a specific database
class UserService:
    def __init__(self):
        self.db = PostgresDatabase()  # Hard-coded dependency

# Better — inject the dependency
class UserService:
    def __init__(self, db):  # Accept any database-like object
        self.db = db
```

**Python-specific techniques for DIP**:
- Constructor injection (most common and recommended)
- Accept callables as factories instead of concrete classes
- Use `typing.Protocol` to define the expected interface without inheritance
- Use default parameter values to provide convenient defaults while allowing injection:
  ```python
  def fetch_data(url, session=None):
      session = session or requests.Session()
  ```

---

## Test-Driven Development

### The TDD Cycle
1. **Red**: Write a failing test that describes the desired behavior
2. **Green**: Write the minimum code to make the test pass
3. **Refactor**: Clean up the code while keeping tests green

### TDD as a Design Tool
TDD isn't just about testing — it's about design feedback. If a test is hard to write, the
design has a problem:

| Hard-to-test symptom | Likely design issue |
|---|---|
| Need to mock many dependencies | Too much coupling, violates DIP |
| Need to set up complex state | Class has too many responsibilities (SRP) |
| Can't test a method in isolation | Hidden dependencies or global state |
| Test requires specific call order | Fragile temporal coupling |
| Need to test private methods | Public interface is incomplete or unclear |

### Testability Checklist for Code Review:
- **Pure functions**: Can this logic be extracted into a pure function (no side effects)?
  Pure functions are trivially testable.
- **Dependency injection**: Are dependencies passed in or hard-coded? Can a test provide
  fakes/stubs?
- **Seams**: Are there clear boundaries where you can substitute test doubles?
- **State**: Is state minimal, explicit, and observable? Or hidden and global?
- **Side effects**: Are side effects (I/O, network, DB) isolated at the edges of the
  system, away from business logic?

### When Planning Changes, Suggest Tests First:
```python
# Planning a new feature? Start with what the tests would look like:
def test_discount_applied_for_orders_over_100():
    calculator = PriceCalculator()
    result = calculator.calculate(subtotal=150.00, discount_threshold=100.00)
    assert result.discount_applied is True
    assert result.total == 135.00  # 10% discount

# This test immediately clarifies:
# - The interface (PriceCalculator.calculate)
# - The inputs (subtotal, discount_threshold)
# - The outputs (an object with discount_applied and total)
# - The behavior (10% discount above threshold)
```

---

## Applying Principles Together

The principles sometimes tension with each other. Here's how to navigate:

**YAGNI vs. OCP**: OCP says "design for extension." YAGNI says "don't build what you don't
need." Resolution: Write simple code (YAGNI) but structure it so extension won't require
rewriting (OCP). This usually means clean separation of concerns and dependency injection,
NOT building abstract extension frameworks.

**KISS vs. SOLID**: Following SOLID strictly can lead to many small classes. Resolution:
Apply SOLID when classes are big enough to benefit. A 20-line script doesn't need five
classes to satisfy SRP. Use judgment.

**DRY vs. YAGNI**: DRY says don't repeat yourself. YAGNI says don't build abstractions
prematurely. Resolution: It's OK to have some duplication until the pattern is clear. The
"Rule of Three" — wait until you see the same pattern three times before extracting.

### Priority Order for Code Review:
1. **Correctness** — Does it work? Are there bugs?
2. **Testability** — Can it be tested in isolation?
3. **Simplicity** (KISS/YAGNI) — Is it as simple as it can be?
4. **Single Responsibility** — Does each piece do one thing?
5. **Coupling** (DIP) — Are dependencies explicit and injectable?
6. **Extensibility** (OCP/LSP) — Can it be extended without modification?
7. **Style** — Naming, formatting, Pythonic idioms
