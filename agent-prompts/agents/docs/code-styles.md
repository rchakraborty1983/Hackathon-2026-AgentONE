# Coding Style
The following code styles in the workflow source code SHOULD generally be followed.  Do NOT treat these as a MUST fix if the code does not follow them, but do call them out in a code review.

## Regions

- Regions MUST appear in the following order within a class:
  1. Nested Classes (`#region [ClassName]`)
  2. Routed Commands (`#region Routed Commands`)
  3. Dependency Properties (`#region Dependency Properties`)
  4. Construction (`#region Construction`)
  5. Fields (`#region Fields`)
  6. Properties (`#region Properties`)
  7. Methods (`#region Methods`)
- A region MUST only be included if it contains at least one member.
- Regions MUST be used if a class has members belonging to more than one section type.
- Regions MUST NOT be used if a class contains members of only one section type.
- Finalizers (`~ClassName`) MUST be placed in the `Construction` region and be the last method in the section.
- If a class implements the `IDisposable` pattern, `Dispose` methods MUST be the last methods in the `Methods` region.
- There MUST be exactly one blank line immediately after each `#region` directive and immediately before each `#endregion` directive.
- Do **NOT** add comments on the `#endregion` line

### Region Layout — with multiple sections
```csharp
internal class ExampleClass
{
    #region Construction

    public ExampleClass() { }

    #endregion

    #region Fields

    private const string ExampleConstant = "Value";

    #endregion

    #region Properties

    public string ExampleProperty { get; set; }

    #endregion

    #region Methods

    public void ExampleMethod(string name, string value, Type type) { }

    // Dispose methods must be last
    public void Dispose() { }

    #endregion
}
```

### Region Layout — single section (no regions)
```csharp
internal class SimpleClass
{
    public void SimpleMethod() => Console.WriteLine("Hello");
}
```

## Coding Style — Conventions

- `var` MUST only be used for local variable declarations when the type name (excluding namespace) is 16 or more characters in length. Types with names of 15 or fewer characters MUST use the explicit type name. This rule does not apply to fields or properties.
- Static members MUST always be accessed using the class name (e.g., `String.Empty`, `Int64.MaxValue`).
- Properties accessed within their own class MUST be prefixed with `this.` (e.g., `this.PropertyName`).
- Instance methods MUST NOT be prefixed with `this.` when called within the same class (e.g., `MethodName()`).
- Fields MUST NOT be prefixed with `this.` (e.g., `fieldName`).
- The lambda expression syntax (`=>`) MUST be used for any method body consisting of a single statement.
- When calling a method that accepts more than 3 arguments, each argument MUST be passed using named argument syntax.

### `var` Usage Rule

| Type Name Length | Use `var`? | Example |
|---|---|---|
| ≤ 15 chars | No | `List<int> list = new();` |
| ≥ 16 chars | Yes | `var applicationSettings = provider.OpenWebConfig(path);` |

### Property, Method, and Field Access

| Member Type | Use `this.`? | Example |
|---|---|---|
| Property | Yes | `this.PropertyName` |
| Method | No | `MethodName()` |
| Field | No | `fieldName` |

### Named Arguments (> 3 parameters)
```csharp
// Correct
OpenDialog(path: filePath, readOnly: false, encoding: Encoding.UTF8, validate: true);

// Incorrect
OpenDialog(filePath, false, Encoding.UTF8, true);
```
