# WorkView Developer Quick Reference

**Last Updated:** 2026-03-17 (high-priority gap pass — added REST API Models project, service interface contracts, FilterQuery object model, InterfaceServices Writers/DataContracts; checklist items 17–20)

## Purpose

Quick reference for common WorkView development patterns, debugging, and file locations. For comprehensive documentation, see [WorkView Developer Guide](./WORKVIEW_DEVELOPER_GUIDE.md).

## Four Client & Tool Perspectives

> Full details in [Developer Guide Section 1.5](./WORKVIEW_DEVELOPER_GUIDE.md#15-four-client--tool-perspectives)

| Perspective | What it is | Key Code Location |
|---|---|---|
| **OnBase Studio** | Config tool (WPF desktop) | `Libraries/Hyland.Core.Studio.WorkView/` |
| **Web Client** | Browser-based client | `Applications/Hyland.Applications.Web.Client/Workview/` + `WorkViewController.cs` |
| **Unity Client** (desktop) | Canvas desktop app | `WorkView/Hyland.WorkView.InterfaceServices/` (HTML renderer) |
| **App Server** | Hosts all runtime services | `Applications/Hyland.Applications.Server/` |

**⚠️ NAMING: "Unity Client" ≠ "Unity SDK"**
- **Unity Client** = the OnBase desktop application (`Hyland.Canvas.Client`)
- **Hyland Unity SDK** = the public developer API (`Libraries/Hyland.Unity/WorkView/`)

---

## Layer Architecture Quick Reference

WorkView is organized in a clean layered architecture with two primary stacks:

**Hyland Unity SDK Layer** (`Libraries/Hyland.Unity/WorkView/`)
- Customer-facing SDK for external developers
- Public API with backward compatibility guarantees
- Wraps Core implementation
- ⚠️ This is NOT the Unity Client (desktop app) — it is a developer SDK

**Core Stack** (`WorkView/Hyland.WorkView/` and `WorkView/Hyland.WorkView.Core/`)
- Internal implementation
- Interface contracts (Hyland.WorkView) + Implementations (Hyland.WorkView.Core)
- Data access, providers, services

**⚠️ CRITICAL RULE: Changes in one layer often require parity changes in the other!**

### Layer Flow
```
UI Layer (Web Client browser | Unity Client desktop | External Access | StatusView)
    ↓
HTTP Handler Layer (WorkViewController for Web Client)
InterfaceServices HTML Renderer (for Unity Client)
    ↓
Service Layer (IFilterService, IAttributeService)
    ↓
Provider Layer (IApplicationProvider, IClassProvider)
    ↓
Data Access Layer (IDataAccessFacade, FilterQuery)
    ↓
Database Layer (rmobject, rmclass, rmapplication)

OnBase Studio → Config Repository (separate path, not runtime stack)
```

## Common Patterns

### Service Registration
All services registered in: `WorkView/Hyland.WorkView.Core/WorkViewStartup.cs`
- Method: `AddWorkViewServices(IServiceCollection services)`
- New services must be registered here

### Provider Pattern
- Providers return domain objects: `IObject`, `IAction`, `ICoreObjectList<T>`
- Use services for business logic operations
- Avoid static methods - use dependency injection

### Test Inheritance
All tests inherit from: `WorkView/Tests/Hyland.WorkView.UnitTests/TestBase.cs`
- Handles session setup/teardown
- Provides test data and helpers

## Top 10 Debugging Checklist

1. **Layer Parity** - Did you update both Hyland Unity SDK and Core implementation?
2. **Service Registration** - Is new service registered in WorkViewStartup (and WorkViewInterfaceServicesStartup if Unity Client)?
3. **Test Coverage** - Does TestBase setup cover your scenario?
4. **Security** - Are permission checks applied at correct layer?
5. **Null Checks** - WorkView objects can be null - always validate
6. **Filter Context** - Are filter constraints applied correctly?
7. **Transaction Scope** - Long operations need proper transaction management
8. **Attribute Types** - Type validation happens at multiple layers
9. **Action Execution** - Actions have complex validation and rollback logic
10. **REST API Contracts** - Changes to DTOs may break external clients
11. **Client Path** - Is the issue Web Client only, Unity Client only, or both? (Different code paths)
12. **Config Cache** - Did Studio config change require App Server cache refresh?
13. **DataAccess vs Data** - Metadata bugs (config load/save) → `DataAccess/`; filter execution bugs → `Data/ObjectQueryBuilder.cs`
14. **Facade access** - New service not found by callers? Check `ServiceAccess.cs` and `DataAccessFacade.cs` in `Facades/`
15. **Calculated attr chain** - Wrong calculated value → start at `EquationProcessor.cs`, not just `EquationManifestService.cs`
16. **Unity Client rules** - Field visibility/readonly/required wrong in desktop? → `InterfaceServices/RuleBuilder/OperationTypes/`
17. **REST field missing** - Field in DB but not in REST response? Check `WebApi.Models/` DTO project FIRST, then `ModelBuilder.cs`
18. **Service interface location** - Looking for IXxxService? → `Hyland.WorkView/Services/`; implementations → `Hyland.WorkView.Core/Services/`
19. **Filter result wrong, not SQL** - Filter query built wrong before SQL? → `Core/FilterQuery/FilterQuery.cs` and `Props/`
20. **Unity Client screen render broken** - Full screen/view HTML wrong? → `InterfaceServices/Writers/ScreenWriter.cs` or `ViewWriter.cs`

## Critical File Locations

### Core Interfaces (Domain Contracts)
- `c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView\IApplication.cs` - Application metadata
- `c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView\IClass.cs` - Class/schema definition
- `c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView\IObject.cs` - Object instances
- `c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView\IFilter.cs` - Query/filter definitions
- `c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView\IAttribute.cs` - Attribute definitions
- `c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView\IAction.cs` - Action/operation definitions

### Service Interface Contracts (Hyland.WorkView/Services/) ⭐
All service contracts live here — always find the interface before the implementation:
- `c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView\Services\IObjectService.cs`
- `c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView\Services\IFilterService.cs`
- `c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView\Services\IFilterQueryService.cs`
- `c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView\Services\IAttributeService.cs`
- `c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView\Services\IPermissionService.cs`
- `c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView\Services\` — 36 IXxxService.cs files total
- Implementations: `WorkView\Hyland.WorkView.Core\Services\`

### FilterQuery Object Model (Core/FilterQuery/) ⭐
The typed request object between FilterQueryService and ObjectQueryBuilder:
- `c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView.Core\FilterQuery\FilterQuery.cs` - Standard filter query
- `c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView.Core\FilterQuery\ClassFilterQuery.cs` - Class-scoped query
- `c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView.Core\FilterQuery\UnityScriptFilterQuery.cs` - Script-sourced query
- `c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView.Core\FilterQuery\WCFFilterQuery.cs` - WCF-sourced query
- `c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView.Core\FilterQuery\LobFilterQuery.cs` - LOB-sourced query
- `c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView.Core\FilterQuery\DynamicQuery\DynamicQuery.cs` - Ad-hoc runtime query
- `c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView.Core\FilterQuery\Props\` - Query construction props (CreateFilterQueryProps, FilterQueryConstraintProps, etc.)
- Interface contracts: `c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView\FilterQuery\`

### Core Implementation
- `c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView.Core\Application.cs` - Application impl
- `c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView.Core\Class.cs` - Class impl
- `c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView.Core\Object.cs` - Object impl (87KB - large core class)
- `c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView.Core\Filter.cs` - Filter impl
- `c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView.Core\WorkViewStartup.cs` - Service registration
- `c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView.Core\WorkViewUtility.cs` - Core utilities (145KB)

### Unity API (Public SDK)
- `c:\OnBase\DEV\Core\OnBase.NET\Libraries\Hyland.Unity\WorkView\Application.cs` - Unity wrapper
- `c:\OnBase\DEV\Core\OnBase.NET\Libraries\Hyland.Unity\WorkView\Class.cs` - Unity wrapper
- `c:\OnBase\DEV\Core\OnBase.NET\Libraries\Hyland.Unity\WorkView\Object.cs` - Unity wrapper
- `c:\OnBase\DEV\Core\OnBase.NET\Libraries\Hyland.Unity\WorkView\ClientFilterQuery.cs` - Filter queries

### Data Access & Providers
- `c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView\Providers\` - Provider interfaces
- `c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView\Data\` - Data access interfaces
- `c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView.Core\Data\` - Data access implementations

### Core Services
- `c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView.Core\Services\ObjectService\ObjectService.cs` - Object CRUD coordinator
- `c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView.Core\Services\FilterService.cs` - Filter operations
- `c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView.Core\Services\FilterQueryService.cs` - SQL generation & execution
- `c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView.Core\Services\AttributeEncryptionService.cs` - Encrypted attributes
- `c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView.Core\Services\CalendarViewService.cs` - Calendar views
- `c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView.Core\Data\ObjectQueryBuilder.cs` - SQL query builder ⭐
- `c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView.Core\ObjectDataAccessFactory.cs` - Data access variant selector ⭐
- `c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView.Core\MacroStringManager.cs` - Macro expansion (90KB) ⭐
- `c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView.Core\ModelBuilder.cs` - Model construction (108KB) ⭐

### REST APIs — Controllers
- `c:\OnBase\DEV\Core\OnBase.NET\WorkView\RestApis\Hyland.OnBase.WorkView.Core.WebApi\Controllers\WVControllerBase.cs` - Base class ⭐
- `c:\OnBase\DEV\Core\OnBase.NET\WorkView\RestApis\Hyland.OnBase.WorkView.Core.WebApi\Controllers\` — Application, Class, Object, Filter, Attribute, Calendar, Document, DataSet, FilterBar, NotificationBar, Schema, UserSettings, HealthCheck

### REST APIs — Models / DTOs ⭐ (separate project — always check here for field shape changes)
- `c:\OnBase\DEV\Core\OnBase.NET\WorkView\RestApis\Hyland.OnBase.WorkView.Core.WebApi.Models\ObjectModels\` — ObjectCreateModel, ObjectUpdateModel, ObjectResultModel, AbbreviatedObjectModel, ObjectQueryModel
- `c:\OnBase\DEV\Core\OnBase.NET\WorkView\RestApis\Hyland.OnBase.WorkView.Core.WebApi.Models\FilterModels\` — FilterModel, FilterResultCollectionModel, FilterQueryModel, ConstraintModel, ColumnModel, FilterDynamicQueryModel
- `c:\OnBase\DEV\Core\OnBase.NET\WorkView\RestApis\Hyland.OnBase.WorkView.Core.WebApi.Models\CalendarModels\` — CalendarEventModel, CalendarDisplayModeModel
- `c:\OnBase\DEV\Core\OnBase.NET\WorkView\RestApis\Hyland.OnBase.WorkView.Core.WebApi.Models\JsonConverters\` — FilterQueryModelJsonConverter
- Root models: AttributeModel, ClassModel, DataSetModel, FilterBarModel, NotificationBarItemModel, ValidationProblemModel
- ⚠️ REST field missing from response? Check this project BEFORE ModelBuilder.cs

### REST APIs — Auth / Infrastructure
- `c:\OnBase\DEV\Core\OnBase.NET\WorkView\RestApis\Hyland.OnBase.WorkView.Core.WebApi\Authorization\BearerTokenAuthorizeFilter.cs` — bearer token auth
- `c:\OnBase\DEV\Core\OnBase.NET\WorkView\RestApis\Hyland.OnBase.WorkView.Core.WebApi\Authorization\OnBaseSessionCookieAuthorizeFilter.cs` — session cookie auth
- `c:\OnBase\DEV\Core\OnBase.NET\WorkView\RestApis\Hyland.OnBase.WorkView.Core.WebApi\Services\WorkViewSessionService.cs` — REST session management

### Web Client (Browser)
- `c:\OnBase\DEV\Core\OnBase.NET\Applications\Hyland.Applications.Web.Client\Workview\` - Web pages (ASPX)
- `c:\OnBase\DEV\Core\OnBase.NET\Libraries\Hyland.Controls.Web\Workview\Handlers\WorkViewController.cs` - HTTP handler / SOA bridge ⭐
- Key pages: `filterresults.aspx`, `filterPop.aspx`, `objectPop.aspx`, `CalendarView.aspx`, `SelectApplication.aspx`
- `c:\OnBase\DEV\Core\OnBase.NET\Applications\Hyland.Applications.Web.Client\Workview\ScreenBase.cs` - Screen base class

### Unity Client / Canvas Client (Desktop)
- `c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView.InterfaceServices\WorkViewInterfaceServicesStartup.cs` - Service registration ⭐
- `c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView.InterfaceServices\ApplicationServerViewWriter\Controls\IWorkViewScreen.cs` - Screen interface
- `c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView.InterfaceServices\LockObjectManager.cs` - Object locking
- `c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView.InterfaceServices\ApplicationServerViewWriter\` - Full HTML renderer

### OnBase Studio (Configuration Tool)
- `c:\OnBase\DEV\Core\OnBase.NET\Libraries\Hyland.Core.Studio.WorkView\WorkViewStudioUtils.cs` - Studio utilities ⭐
- `c:\OnBase\DEV\Core\OnBase.NET\Libraries\Hyland.Core.Studio.WorkView\Dialogs\WorkViewDoctor.xaml` - WorkView Doctor diagnostic tool
- `c:\OnBase\DEV\Core\OnBase.NET\Libraries\Hyland.Core.Studio.WorkView\ImportExport\WorkViewImportXMLConverter.cs` - Import/export
- `c:\OnBase\DEV\Core\OnBase.NET\Libraries\Hyland.Core.Studio.WorkView\Controls\` - All Studio UI controls
- `c:\OnBase\DEV\Core\OnBase.NET\WorkView\Tests\Hyland.WorkView.Config.Tests.Common\TestUtils\StudioTestUtility.cs` - Studio test helpers

### Per-Entity Data Access Layer (`DataAccess/`)
- `c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView.Core\DataAccess\` - 55+ entity DA files (`AttributeDataAccess.cs`, `ClassDataAccess.cs`, `FilterDataAccess.cs`, `SequenceDataAccess.cs`, etc.)
- Interface contracts: `c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView\Data\` - all `IXxxDataAccess.cs`
- **Use this for:** config/metadata load-save bugs (NOT filter execution — that's `Data/ObjectQueryBuilder.cs`)

### Facade / Access Aggregation Layer (`Facades/`)
- `c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView.Core\Facades\ServiceAccess.cs` - Aggregates all services
- `c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView.Core\Facades\DataAccessFacade.cs` - Aggregates all DA classes
- `c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView.Core\Facades\CoreProviderAccess.cs` - Aggregates all providers
- `c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView.Core\Facades\ObjectFacade.cs` - Object CRUD aggregator
- `c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView.Core\Facades\WorkflowProviderAccess.cs` - Workflow-specific access

### Calculated Attributes Deep Chain
- `c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView.Core\CalculatedAttributes\EquationManifest.cs` - Equation parser
- `c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView.Core\CalculatedAttributes\EquationProcessor.cs` - Equation evaluator ⭐
- `c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView.Core\CalculatedAttributes\TransientResolver.cs` - Dynamic value resolver
- `c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView.Core\CalculatedAttributes\Functions\` - Built-in function implementations

### Server-Side Doctor Framework
- `c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView.Core\Doctor\TestRunner.cs` - Diagnostic test runner
- `c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView.Core\Doctor\IWorkViewDoctorTest.cs` - Test interface
- `c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView.Core\Doctor\DataObjectUpdater\` - Bulk data maintenance utilities
- `c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView.Core\Doctor\Maintenance\` - Maintenance operations

### Report Processor
- `c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView.Core\ReportProcessor\ReportProcessor.cs` - Report orchestrator
- `c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView.Core\ReportProcessor\TemplateGenerator.cs` - Template engine
- `c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView.Core\ReportProcessor\HTMLGenerator.cs` - HTML output

### XML Data Mapping & Transfer
- `c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView.Core\XmlDataMapping\WcfDataManager.cs` - WCF data manager
- `c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView.Core\XmlDataMapping\LobBrokerDataManager.cs` - LOB data manager
- `c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView.Core\Transfer\AttributeMappingResolverFactory.cs` - Transfer mapping factory
- `c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView.Core\Transfer\AttributeValueMappingResolver.cs` - Value mapping resolver

### InterfaceServices — Additional Subsystems (Unity Client)
- `c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView.InterfaceServices\ApplicationServerViewWriter\Writers\ScreenWriter.cs` - Renders object detail screen to HTML ⭐
- `c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView.InterfaceServices\ApplicationServerViewWriter\Writers\ViewWriter.cs` - Renders filter results view to HTML ⭐
- `c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView.InterfaceServices\ApplicationServerViewWriter\DataContracts\DisplayObject.cs` - Unity Client display object contract ⭐
- `c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView.InterfaceServices\ApplicationServerViewWriter\DataContracts\` - All Unity Client data contracts (parallel to Web Client Contracts/)
- `c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView.InterfaceServices\ApplicationServerViewWriter\ViewItemUtilities\` - View item rendering (Attribute, CheckList, Filters, Folders, Screen partials)
- `c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView.InterfaceServices\RuleBuilder\RuleBuilderResolver.cs` - Screen rule entry point ⭐
- `c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView.InterfaceServices\RuleBuilder\OperationTypes\` - 9 rule type resolvers
- `c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView.InterfaceServices\QueryResultWriters\JsonQueryResultWriter.cs` - JSON result serializer ⭐
- `c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView.InterfaceServices\Services\ObjectTitleService.cs` - Object title resolution
- `c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView.InterfaceServices\Services\UserLayoutSettingsService.cs` - Layout persistence
- `c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView.InterfaceServices\Interfaces\` - Unity Client–specific interfaces
- `c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView.InterfaceServices\Providers\` - Unity Client–specific providers

### Shared Models
- `c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView.Shared\Models\CompositeKeyModel.cs` - Composite key shared model
- `c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView.Shared\Models\LookaheadConstraintResultListModel.cs` - Lookahead constraint results

### ComponentDoctor Utility
- `c:\OnBase\DEV\Core\OnBase.NET\WorkView\Utilities\Hyland.WorkView.ComponentDoctor\` - Standalone WPF tool for DB-level component data repair (NOT the Studio WorkView Doctor dialog)

### Tests
- `c:\OnBase\DEV\Core\OnBase.NET\WorkView\Tests\Hyland.WorkView.UnitTests\TestBase.cs` - Test base class ⭐
- `c:\OnBase\DEV\Core\OnBase.NET\WorkView\Tests\Hyland.WorkView.Test.Framework\TestUtility.cs` - Shared test framework
- `c:\OnBase\DEV\Core\OnBase.NET\WorkView\Tests\Hyland.WorkView.Config.Tests.Common\TestUtils\StudioTestUtility.cs` - Studio config test helpers ⭐
- `c:\OnBase\DEV\Core\OnBase.NET\WorkView\Tests\Hyland.WorkView.IntegrationTests\` - Core integration tests (live DB)
- `c:\OnBase\DEV\Core\OnBase.NET\WorkView\Tests\Hyland.WorkView.InterfaceServices.IntegrationTests\` - Unity Client integration tests
- `c:\OnBase\DEV\Core\OnBase.NET\WorkView\Tests\Hyland.OnBase.WorkView.RestApi.IntegrationTests\` - REST API integration tests
- `c:\OnBase\DEV\Core\OnBase.NET\WorkView\Tests\Hyland.WorkView.Config.UnitTests\` - Studio config unit tests
- `c:\OnBase\DEV\Core\OnBase.NET\WorkView\Tests\Hyland.WorkView.Config.IntegrationTests\` - Studio config integration tests
- `c:\OnBase\DEV\Core\OnBase.NET\WorkView\Tests\UIAutomation\Hyland.WorkView.UI.Tests\` - End-to-end UI automation tests
- `c:\OnBase\DEV\Core\OnBase.NET\Tests\Hyland.Core.Workview.Test\Core\WorkviewUtilityTest.cs` - Utility tests
- `c:\OnBase\DEV\Core\OnBase.NET\Tests\Hyland.Core.Unity.Test\Services\Workview\` - Unity SDK tests (not Unity Client)

## Domain Model Quick Reference

> Full entity details with file locations in [Developer Guide Section 1.2](./WORKVIEW_DEVELOPER_GUIDE.md#12-key-concepts--domain-model)

**Top-level Hierarchy:**
- **Application** → Classes, Filters, FilterBars, Actions, DataSets, CalendarViews, MobileApps, ClientScripts, GlobalStyles
- **Class** → Attributes, Screens, ClassTriggers, Notifications, DocFolders, Partials, Sequences, FullTextAttributes
- **Object** → AttributeValues, ObjectDocuments, ObjectHistory/Instances, EventLogs, ObjectHeaderFooter, NotificationBarItems
- **Filter** → ViewAttributes, FilterConstraints, FilterEntryAttributes, FilterSorts, SubFilters, ConstraintSets, FilterUserOverrides

**Attribute types:** Alphanumeric, Numeric, Date, Time, Reference, LookupList, EncryptedAlphanumeric, DataSet
**Attribute modifiers:** Masks, KeyTypes/KeyTypeMaps (composite keys), FullTextAttribute, Calculated (via EquationManifest), Sequence

**Entity quick-lookup:**

| Entity | Interface | Key File |
|---|---|---|
| Application | `IApplication` | `Hyland.WorkView/IApplication.cs` |
| Class | `IClass` | `Hyland.WorkView/IClass.cs` |
| Object | `IObject` | `Hyland.WorkView.Core/Object.cs` (87KB) |
| Filter | `IFilter` | `Hyland.WorkView/IFilter.cs` |
| Attribute | `IAttribute` | `Hyland.WorkView/IAttribute.cs` |
| AttributeValue | `IAttributeValue` | `Hyland.WorkView.Core/AttributeValue.cs` (97KB) |
| Action | `IAction` | `Hyland.WorkView/Actions/IAction.cs` |
| Screen | `IScreen` | `Hyland.WorkView/IScreen.cs` |
| CalendarView | `ICalendarView` | `Hyland.WorkView/ICalendarView.cs` |
| ClassTrigger | `IClassTrigger` | `Hyland.WorkView/IClassTrigger.cs` |
| Notification | `INotification` | `Hyland.WorkView/INotification.cs` |
| FilterBar | `IFilterBar` | `Hyland.WorkView/IFilterBar.cs` |
| DataSet | `IDataSet` | `Hyland.WorkView/IDataSet.cs` |
| Constraint | `IConstraint` | `Hyland.WorkView/IConstraint.cs` |
| ConstraintSet | `IConstraintSet` | `Hyland.WorkView/IConstraintSet.cs` |
| ObjectHistory | `IObjectHistory` | `Hyland.WorkView/IObjectHistory.cs` |
| EventLog | `IEventLog` | `Hyland.WorkView/IEventLog.cs` |
| ObjectDocument | `IObjectDocument` | `Hyland.WorkView/IObjectDocument.cs` |
| DocFolder | `IDocFolder` | `Hyland.WorkView/IDocFolder.cs` |
| Sequence | `ISequence` | `Hyland.WorkView/ISequence.cs` |
| MobileApp | `IMobileApp` | `Hyland.WorkView/IMobileApp.cs` |
| EncryptedAttr | `IEncryptedAlphanumeric` | `Hyland.WorkView/IEncryptedAlphanumeric.cs` |
| FullTextAttr | `IFullTextAttribute` | `Hyland.WorkView/IFullTextAttribute.cs` |
| ClientScript | `IClientScript` | `Hyland.WorkView/IClientScript.cs` |
| QueryResults | `IQueryResults` | `Hyland.WorkView/IQueryResults.cs` |

## External Data Source Types Quick Reference

When a filter returns no results or wrong data for external-source objects, identify which access variant is in use:

| Filter Type | Data Access Class | Configured Via |
|---|---|---|
| Standard | `StandardObjectDataAccess` | Normal OnBase DB objects |
| ODBC Linked Server | `ExternalLinkedServerObjectDataAccess` | SQL linked server |
| ODBC Direct | `ExternalODBCObjectDataAccess` | ODBC connection string |
| WCF Service | `ExternalWCFObjectDataAccess` | WCF endpoint config |
| LOB/EIS Broker | `ExternalLOBBrokerObjectDataAccess` | LOB broker config |
| Unity Script | `ExternalUnityScriptObjectDataAccess` | Unity Script on filter |

Factory: `WorkView/Hyland.WorkView.Core/ObjectDataAccessFactory.cs`
SQL generation: `WorkView/Hyland.WorkView.Core/Data/ObjectQueryBuilder.cs`

## Service Layer Quick Reference

> Full service table in [Developer Guide Section 1.6](./WORKVIEW_DEVELOPER_GUIDE.md#16-service-layer-map)

**When debugging, find the service for the area you're in:**

| Area | Primary Service | Registration |
|---|---|---|
| Object CRUD | `ObjectService` (4 partial files) | `WorkViewStartup.cs` |
| Filter execution | `FilterQueryService` | `WorkViewStartup.cs` |
| Filter metadata | `FilterService` | `WorkViewStartup.cs` |
| Attribute values | `AttributeService` + `AttributeValue.cs` | `WorkViewStartup.cs` |
| Encrypted attributes | `AttributeEncryptionService` | `WorkViewStartup.cs` |
| Composite keys | `CompositeKeyService` | `WorkViewStartup.cs` |
| Calculated attrs | `EquationManifestService` | `WorkViewStartup.cs` |
| Macros/display | `MacroStringManager` (90KB) | internal |
| Model building | `ModelBuilder` (108KB) | internal |
| Calendar views | `CalendarViewService` | `WorkViewStartup.cs` |
| Notifications | `NotificationService` | `WorkViewStartup.cs` |
| Object history | `ObjectHistoryService` | `WorkViewStartup.cs` |
| Object documents | `ObjectDocumentService` | `WorkViewStartup.cs` |
| Filter copy/share | `FilterCopyService` | `WorkViewStartup.cs` |
| Full-text search | `FullTextFilterEntryAttributeService` | `WorkViewStartup.cs` |
| Sequences | `SequenceService` | `WorkViewStartup.cs` |
| Unity Scripts | `UnityScriptService` | `WorkViewStartup.cs` |
| Permissions | `PermissionService` | `WorkViewStartup.cs` |
| DataSets | `DataSetValueService` | `WorkViewStartup.cs` |
| Mobile | `MobileComponentService` | `WorkViewStartup.cs` |
| Display templates | `DisplayTemplateService` | `WorkViewStartup.cs` |
| Outlook | `OutlookService` | `WorkViewStartup.cs` |
| Object sync | `ObjectSyncService` | `WorkViewStartup.cs` |
| Unity Client rendering | `ApplicationServerViewWriter` | `WorkViewInterfaceServicesStartup.cs` |
| Live update templates | `LiveUpdateDisplayTemplateService` | `WorkViewInterfaceServicesStartup.cs` |

## Large Files to Know

| File | Size | What It Does |
|---|---|---|
| `WorkViewUtility.cs` | 145KB | Shared helpers — formatting, parsing, validation |
| `ModelBuilder.cs` | 108KB | Builds client-facing data models from provider data |
| `MacroStringManager.cs` | 90KB | `{macro}` expansion in names, constraints, templates |
| `AttributeValue.cs` | 97KB | All attribute value read/write, type coercion, validation |
| `Object.cs` | 87KB | Core object: security, attributes, docs, history, state |

## Recent Learnings

*This section will be updated incrementally as Jira cards are completed*

---

## Additional Resources

- [WorkView Developer Guide](./WORKVIEW_DEVELOPER_GUIDE.md) - Comprehensive knowledge base with architecture deep dive
  - Section 1.9 — Internal Subsystems (DataAccess layer, Facades, CalculatedAttributes chain, Doctor runtime, ReportProcessor, XmlDataMapping/Transfer, InterfaceServices subsystems, Shared models, internal utilities, ComponentDoctor, complete test inventory)
- [Jira Solutions Archive](./jira-solutions/INDEX.md) - Searchable past issue resolutions
