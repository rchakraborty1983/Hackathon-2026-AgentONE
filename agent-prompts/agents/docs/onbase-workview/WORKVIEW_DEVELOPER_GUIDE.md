# WorkView Developer Guide - Living Knowledge Base

**Last Updated:** 2026-03-16

This is a living knowledge base for WorkView development in OnBase. It captures architecture, patterns, debugging techniques, and lessons learned from solving real issues.

---

## Table of Contents

1. [Architecture Deep Dive](#1-architecture-deep-dive)
   - [1.1 Layer Architecture](#11-layer-architecture)
   - [1.2 Key Concepts & Domain Model](#12-key-concepts--domain-model)
   - [1.3 Integration Points](#13-integration-points)
   - [1.4 Critical Files Map](#14-critical-files-map)
   - [1.5 Four Client & Tool Perspectives](#15-four-client--tool-perspectives)
   - [1.6 Service Layer Map](#16-service-layer-map)
   - [1.7 External Data Access Variants](#17-external-data-access-variants)
   - [1.8 Key Large Files & Subsystems](#18-key-large-files--subsystems)
   - [1.9 Internal Subsystems & Supporting Infrastructure](#19-internal-subsystems--supporting-infrastructure)
2. [Development Patterns](#2-development-patterns) *(to be expanded)*
3. [Testing Guide](#3-testing-guide) *(to be expanded)*
4. [Debugging & Troubleshooting](#4-debugging--troubleshooting) *(to be expanded)*
5. [Code Review Checklist](#5-code-review-checklist) *(to be expanded)*
6. [Known Gotchas & Edge Cases](#6-known-gotchas--edge-cases) *(to be expanded)*
7. [Reference Materials](#7-reference-materials)
8. [Appendix: Change Log](#appendix-change-log)

---

## 1. Architecture Deep Dive

### 1.1 Layer Architecture

WorkView is built with a clean layered architecture that separates concerns and maintains flexibility:

```
┌────────────────────────────────────────────────────────────┐
│                      UI Layer                              │
│  Web Client (browser) | Unity Client (Canvas, desktop)    │
│  External Access Client | StatusView                      │
│  OnBase Studio (configuration only - not runtime)         │
└────────────────────────┬───────────────────────────────────┘
                         │
┌────────────────────────▼───────────────────────────────────┐
│              HTTP Handler Layer                            │
│           WorkViewController (SOA bridge)                  │
└────────────────────────┬───────────────────────────────────┘
                         │
┌────────────────────────▼───────────────────────────────────┐
│         Data Contracts / Service Layer                     │
│  DisplayObject | ObjectModel | FilterResultsModel          │
│           Serialization (JSON/XML/WCF)                     │
└────────────────────────┬───────────────────────────────────┘
                         │
┌────────────────────────▼───────────────────────────────────┐
│        Business Logic / Services Layer                     │
│  IFilterService | IEventLogService | IAttributeService     │
│  IFilterQueryService | ICalendarViewService                │
└────────────────────────┬───────────────────────────────────┘
                         │
┌────────────────────────▼───────────────────────────────────┐
│          Provider Layer (Repositories)                     │
│  IApplicationProvider | IClassProvider | IFilterProvider   │
│  IActionProvider | IObjectProvider (with caching)          │
└────────────────────────┬───────────────────────────────────┘
                         │
┌────────────────────────▼───────────────────────────────────┐
│      Data Access Layer / SQL Generation                    │
│  IDataAccessFacade | WorkviewDataAccess                    │
│  FilterQuery | WorkflowTablesQueryWrapper | SQLString      │
└────────────────────────┬───────────────────────────────────┘
                         │
┌────────────────────────▼───────────────────────────────────┐
│                 Database Layer                             │
│  rmobject | rmclass | rmapplication | rmfilter             │
│  rmobjectinstance[classID] | rmeventlog                    │
└────────────────────────────────────────────────────────────┘
```

#### Two Primary Stacks

**Unity API Layer** ([Libraries/Hyland.Unity/WorkView/](c:\OnBase\DEV\Core\OnBase.NET\Libraries\Hyland.Unity\WorkView))
- Public SDK for external developers and customers
- Backward compatibility guarantees
- Wraps Core implementation
- Located in `Hyland.Unity.WorkView` namespace

**Core Stack** ([WorkView/](c:\OnBase\DEV\Core\OnBase.NET\WorkView))
- Internal implementation
- Interface contracts: `Hyland.WorkView` (interfaces only, no dependencies on Core)
- Core implementation: `Hyland.WorkView.Core` (concrete classes)
- Services, providers, data access

**⚠️ CRITICAL: Layer Parity Rule**
Changes in one layer often require corresponding changes in the other to maintain feature parity. Always check both stacks when making changes.

---

### 1.2 Key Concepts & Domain Model

WorkView is OnBase's object-oriented data management system. It provides:
- Dynamic content management with business objects
- Sophisticated filtering and query capabilities
- Action processing and workflow integration
- Multi-source data aggregation

#### Core Domain Entities

**IApplication** - Top-level organizational unit
- Properties: ApplicationID, Name, GroupName, Flags, DefaultFilterID
- Contains: Classes, Filters, UnityScripts, MobileApps, FilterBars, Actions, CalendarViews
- Location: [WorkView/Hyland.WorkView/IApplication.cs](c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView\IApplication.cs)

**IClass** - Object type definition (schema/metadata)
- Properties: ClassID, Name, DisplayName, Attributes, BaseClass, ComponentClasses
- Contains: Attributes, DocFolders, Forms, FullTextAttributes, ClassTriggers, Notifications, Screens
- Capabilities: Class inheritance, extension, transformation, validation rules
- Location: [WorkView/Hyland.WorkView/IClass.cs](c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView\IClass.cs)

**IObject** - Instance of a Class (actual data record)
- Properties: ObjectID, ClassID, Name, AttributeValues, Status, CreatedBy, CreatedDate
- Contains: Documents, AttributeValues, History, Folders, Forms, EventLogs, ObjectSync
- Capabilities: Security checks, document attachment, history tracking, attribute validation
- Location: [WorkView/Hyland.WorkView/IObject.cs](c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView\IObject.cs)
- Implementation: [WorkView/Hyland.WorkView.Core/Object.cs](c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView.Core\Object.cs) (87KB - large core class)

**IFilter** - Query definition/saved search
- Properties: FilterID, ClassID, Name, Description, Flags, CatalogNum
- Types: Standard, Association, ODBC external, EIS sourced, Linked external, WCF sourced, LOB sourced, Unity Script sourced
- Contains: ViewAttributes, FixedConstraints, EntryAttributes, FixedSorts, SubFilters, UnityScripts
- Location: [WorkView/Hyland.WorkView/IFilter.cs](c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView\IFilter.cs)

**IAttribute** - Field definition within a Class
- Types: Alphanumeric, Numeric, Date, Time, Reference, LookupList, EncryptedAlphanumeric, DataSet
- Properties: AttributeID, Name, DataType, Mask, LookupList, Security
- Supports: Calculated attributes, composite keys, encryption, masks, full-text indexing
- Location: [WorkView/Hyland.WorkView/IAttribute.cs](c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView\IAttribute.cs)

**IAction** - Business operation/command
- Properties: ActionID, Name, Type, CommandText, UIEffect, Flags
- Types: `IScreenAction` (UI action), `IAdhocAction` (ad-hoc), `IUIAction` (display data action)
- Sub-types: `IDisplayData` — generates report/display output
- Execution: Single objects, filter results, or object collections; can trigger UI effects
- Location: [WorkView/Hyland.WorkView/Actions/IAction.cs](c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView\Actions\IAction.cs)

---

#### Extended Domain Entities (complete picture)

**IScreen** - UI layout definition for object detail views
- Configured in Studio's Screen Designer
- Defines how attributes are arranged when viewing/editing an object
- Rendered by `ApplicationServerViewWriter` for Unity Client; ASPX pages for Web Client
- Location: [WorkView/Hyland.WorkView/IScreen.cs](c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView\IScreen.cs)

**ICalendarView / ICalendarEvent** - Calendar display configuration
- `ICalendarView`: Calendar definition attached to an Application (date range, display fields)
- `ICalendarEvent`: Individual event rendered on the calendar (maps object attributes to date/title)
- Web Client: `CalendarView.aspx` + `CalendarHandler.ashx` (separate handler, NOT WorkViewController)
- REST: `CalendarController.cs`
- Service: `CalendarViewService.cs`; Provider: `CalendarViewProvider.cs`
- Locations: [ICalendarView.cs](c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView\ICalendarView.cs), [ICalendarEvent.cs](c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView\ICalendarEvent.cs)

**IClassTrigger / IClassTriggerBucket** - Event handler configuration
- `IClassTrigger`: An event handler that fires on object Create, Save, or Delete
- `IClassTriggerBucket`: Groups triggers by event type
- Configured in Studio; executed server-side during object operations
- Providers: `ClassTriggerProvider.cs`, `ClassTriggerBucketProvider.cs`
- Locations: [IClassTrigger.cs](c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView\IClassTrigger.cs), [IClassTriggerBucket.cs](c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView\IClassTriggerBucket.cs)

**INotification / INotificationBarItem** - Alert and notification system
- `INotification`: A configured notification rule (sends alert when object state changes)
- `INotificationBarItem`: An individual notification displayed in the notification bar
- Service: `NotificationService.cs`; Provider: `NotificationProvider.cs`
- REST: `NotificationBarController.cs`
- Locations: [INotification.cs](c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView\INotification.cs), [INotificationBarItem.cs](c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView\INotificationBarItem.cs)

**IFilterBar / IFilterBarItem / IFilterBarSubFilter** - Quick navigation filter sets
- `IFilterBar`: A named collection of filters presented as a navigation panel
- `IFilterBarItem`: One entry in a filter bar (can be a filter or sub-filter group)
- `IFilterBarSubFilter`: A sub-filter within a filter bar item
- Service: `FilterBarItemService.cs`; Providers: `FilterBarProvider.cs`, `FilterBarItemProvider.cs`
- REST: `FilterBarController.cs`, `FilterBarItemController.cs`
- Locations: [IFilterBar.cs](c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView\IFilterBar.cs), [IFilterBarItem.cs](c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView\IFilterBarItem.cs)

**IDataSet / IDataSetValue** - External data source integration
- `IDataSet`: A named external data source definition (ODBC, WCF, LOB)
- `IDataSetValue`: A row of data returned from a DataSet query
- Service: `DataSetValueService.cs`; Providers: `DataSetProvider.cs`, `DataSetValueProvider.cs`
- REST: `DataSetController.cs`
- Locations: [IDataSet.cs](c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView\IDataSet.cs), [IDataSetValue.cs](c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView\IDataSetValue.cs)

**IConstraint / IConstraintSet** - Reusable filter constraint rules
- `IConstraint`: A single filter constraint (field, operator, value)
- `IConstraintSet`: A named, reusable group of constraints that can be applied to filters
- Providers: `ConstraintProvider.cs`, `ConstraintSetProvider.cs`
- Studio control: `StudioWorkViewConstraintSetConfiguration.cs`
- Locations: [IConstraint.cs](c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView\IConstraint.cs), [IConstraintSet.cs](c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView\IConstraintSet.cs)

**IAttributeValue** - Actual field value on an object instance
- The runtime value stored against an object for a specific attribute
- Implementation: [WorkView/Hyland.WorkView.Core/AttributeValue.cs](c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView.Core\AttributeValue.cs) (97KB — one of the largest files)
- Location: [IAttributeValue.cs](c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView\IAttributeValue.cs)

**IEncryptedAlphanumeric** - Encrypted attribute value handling
- Wraps attribute values stored with encryption
- Service: `AttributeEncryptionService.cs`; Provider: `EncryptedAlphanumericProvider.cs`
- Location: [IEncryptedAlphanumeric.cs](c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView\IEncryptedAlphanumeric.cs)

**IFullTextAttribute** - Full-text search index configuration
- Maps class attributes into a full-text search index
- Service: `FullTextFilterEntryAttributeService.cs`; Provider: `FullTextAttributeProvider.cs`
- Location: [IFullTextAttribute.cs](c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView\IFullTextAttribute.cs)

**IAttributeKeyType / IAttributeKeyTypeMap** - Composite key definitions
- `IAttributeKeyType`: Defines an attribute as part of a composite key
- `IAttributeKeyTypeMap`: Maps composite key values to keyword values for cross-system linking
- Service: `CompositeKeyService.cs`; Providers: `AttributeKeyTypeProvider.cs`, `AttributeKeyTypeMapProvider.cs`
- Locations: [IAttributeKeyType.cs](c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView\IAttributeKeyType.cs), [IAttributeKeyTypeMap.cs](c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView\IAttributeKeyTypeMap.cs)

**IAttributeMask / IAttributeMaskDefinition** - Input mask definitions
- `IAttributeMask`: Applied mask on an attribute instance
- `IAttributeMaskDefinition`: The mask template definition (e.g., phone number format)
- Providers: `AttributeMaskProvider.cs`, `AttributeMaskDefinitionProvider.cs`
- Locations: [IAttributeMask.cs](c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView\IAttributeMask.cs), [IAttributeMaskDefinition.cs](c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView\IAttributeMaskDefinition.cs)

**ISequence** - Auto-increment field generator
- Provides auto-incrementing numeric values for attribute fields
- Service: `SequenceService.cs`; Provider: `SequenceProvider.cs`
- Location: [ISequence.cs](c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView\ISequence.cs)

**IObjectHistory / IObjectInstance / IEventLog** - Audit trail
- `IObjectHistory`: A history record tracking changes to an object over time
- `IObjectInstance`: A snapshot of an object's data at a point in time
- `IEventLog`: A change event log entry (what changed, when, by whom)
- Services: `ObjectHistoryService.cs`, `EventLogService.cs`
- REST: history included in `ObjectController.cs`
- Locations: [IObjectHistory.cs](c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView\IObjectHistory.cs), [IEventLog.cs](c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView\IEventLog.cs)

**IObjectDocument / IDocFolder / IDocFolderDocType** - Document attachment model
- `IObjectDocument`: A document attached to an object
- `IDocFolder`: A folder within a class that organizes document types
- `IDocFolderDocType`: A document type allowed within a doc folder
- Service: `ObjectDocumentService.cs`; Providers: `ObjectDocumentProvider.cs`, `DocFolderProvider.cs`
- REST: `DocumentController.cs`
- Locations: [IObjectDocument.cs](c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView\IObjectDocument.cs), [IDocFolder.cs](c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView\IDocFolder.cs)

**IObjectHeaderFooter / IObjectHeaderFooterCell** - Object header/footer display
- Defines the header/footer bands rendered above/below object detail screens
- Providers: `ObjectHeaderFooterProvider.cs`, `ObjectHeaderFooterCellProvider.cs`
- Locations: [IObjectHeaderFooter.cs](c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView\IObjectHeaderFooter.cs)

**IObjectSync** - Multi-server object synchronization
- Handles syncing WorkView object state across multiple app servers
- Provider: `ObjectSyncProvider.cs`; Service: `ObjectSyncService.cs`
- Location: [IObjectSync.cs](c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView\IObjectSync.cs)

**IPartial / IPartialAttribute** - Partial/component attribute model
- `IPartial`: A partial view/component of a class, used for embedding sub-data
- `IPartialAttribute`: An attribute within a partial
- Providers: `PartialProvider.cs`, `PartialAttributeProvider.cs` (note: no `IPartialAttributeProvider.cs` interface — uses `PartialProvider.cs`)
- Locations: [IPartial.cs](c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView\IPartial.cs), [IPartialAttribute.cs](c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView\IPartialAttribute.cs)

**IMobileApp / IMobileAppPage / IMobileComponent / IMobileFab** - Mobile application model
- `IMobileApp`: A WorkView mobile app configuration
- `IMobileAppPage`: A page within the mobile app
- `IMobileComponent`: A UI component on a mobile page (list, card, form, etc.)
- `IMobileFab`: A floating action button on a mobile page
- Configured in Studio's MobileDesigner; served via REST API
- Services: `MobileComponentService.cs`; Providers: `MobileAppProvider.cs`, etc.
- Locations: [IMobileApp.cs](c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView\IMobileApp.cs), [IMobileAppPage.cs](c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView\IMobileAppPage.cs)

**IOperation** - Internal operation definition
- Represents a low-level operation that can be triggered as part of action execution
- Provider: `OperationProvider.cs`
- Location: [IOperation.cs](c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView\IOperation.cs)

**IReportManager / IReportProcessor** - Reporting pipeline
- `IReportManager`: Manages the WorkView reporting pipeline
- `IReportProcessor`: Processes report output from DisplayDataActions
- Locations: [IReportManager.cs](c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView\IReportManager.cs), [IReportProcessor.cs](c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView\IReportProcessor.cs)

**IQueryResults / IQueryResultsCursor / IQueryResultWriter** - Query result handling
- `IQueryResults`: The result set from a filter execution
- `IQueryResultsCursor`: Cursor for iterating over large result sets
- `IQueryResultWriter`: Writes query results to output (JSON, CSV, etc.)
- Implementations: `QueryResultsCursor.cs`
- Locations: [IQueryResults.cs](c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView\IQueryResults.cs), [IQueryResultsCursor.cs](c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView\IQueryResultsCursor.cs)

**IFilterUserOverride / IFilterUserOverrideModel** - User-customized filter settings
- Allows users to save personal overrides on filter constraints
- Provider: `FilterUserOverrideModelProvider.cs` (via `IFilterUserOverrideModelProvider`)
- Locations: [IFilterUserOverride.cs](c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView\IFilterUserOverride.cs)

**IClientScript / IUnityScriptProvider** - Unity Script integration
- `IClientScript`: A Unity Script (C# script) attached to a filter or object
- Executes custom logic server-side during filter/object operations
- Provider: `UnityScriptProvider.cs`; Service: `UnityScriptService.cs`
- Location: [IClientScript.cs](c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView\IClientScript.cs)

**IGlobalStyle** - Application-wide CSS/style definitions
- Defines visual styling applied globally across a WorkView application
- Provider: `GlobalStyleProvider.cs`
- Location: [IGlobalStyle.cs](c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView\IGlobalStyle.cs)

**IDottedAddress** - Hierarchical object addressing
- Provides a dotted-notation address for navigating object relationships
- Provider: `DottedAddressProvider.cs`
- Location: [IDottedAddress.cs](c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView\IDottedAddress.cs)

**ILobInfo** - Line of Business (LOB/EIS) data source metadata
- Metadata about an external LOB data source connection
- Provider: `LobInfoProvider.cs`
- Location: [ILobInfo.cs](c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView\ILobInfo.cs)

**ITimerActionProvider** - Scheduled/background action execution
- Provides timer-based action execution (background scheduled operations)
- Provider: `TimerActionProvider.cs`
- Location: [WorkView/Hyland.WorkView/Providers/ITimerActionProvider.cs](c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView\Providers\ITimerActionProvider.cs)

---

#### Domain Model Hierarchy (complete)

```
Application
├── Classes
│   ├── Attributes (with Masks, KeyTypes, FullTextAttributes)
│   ├── Screens (UI layout, configured in Studio)
│   ├── ClassTriggers / ClassTriggerBuckets (event handlers)
│   ├── Notifications (alert rules)
│   ├── DocFolders / DocFolderDocTypes (document organization)
│   ├── Partials / PartialAttributes (sub-data components)
│   └── Sequences (auto-increment generators)
├── Filters
│   ├── FilterViewAttributes (result columns)
│   ├── FilterConstraints (fixed search criteria)
│   ├── FilterEntryAttributes (user-entered criteria)
│   ├── FilterSorts (result ordering)
│   ├── SubFilters (nested filter logic)
│   ├── ConstraintSets (reusable constraint groups)
│   └── FilterUserOverrides (per-user saved settings)
├── FilterBars
│   ├── FilterBarItems
│   └── FilterBarSubFilters
├── Actions
│   ├── ScreenActions (UI actions)
│   ├── AdhocActions
│   └── Operations (low-level operation steps)
├── DataSets / DataSetValues
├── CalendarViews / CalendarEvents
├── MobileApps
│   ├── MobileAppPages
│   ├── MobileComponents
│   └── MobileFabs (floating action buttons)
├── ClientScripts (Unity Scripts)
└── GlobalStyles

Class → Objects
    ├── AttributeValues (field data; see AttributeValue.cs 97KB)
    ├── ObjectDocuments (attached files)
    ├── DocFolders (document organization on the object)
    ├── ObjectHistory / ObjectInstances (audit snapshots)
    ├── EventLogs (change records)
    ├── ObjectHeaderFooter / ObjectHeaderFooterCells
    ├── NotificationBarItems (active alerts for this object)
    └── ObjectSync (multi-server sync state)

Filter → QueryResults / QueryResultsCursor
    → IQueryResultWriter (serializes output)
```

---

### 1.3 Integration Points

#### A. REST API Layer
Location: [WorkView/RestApis/Hyland.OnBase.WorkView.Core.WebApi/](c:\OnBase\DEV\Core\OnBase.NET\WorkView\RestApis\Hyland.OnBase.WorkView.Core.WebApi)

The REST API is split across **two projects** that must be understood together:

**1. Controllers project** (`Hyland.OnBase.WorkView.Core.WebApi/`) — request handling and service orchestration
**2. Models project** (`Hyland.OnBase.WorkView.Core.WebApi.Models/`) — **all request/response DTOs** ← see Section 1.9.14

Controllers:
- [ApplicationController.cs](c:\OnBase\DEV\Core\OnBase.NET\WorkView\RestApis\Hyland.OnBase.WorkView.Core.WebApi\Controllers\ApplicationController.cs) - Application metadata retrieval
- [FilterController.cs](c:\OnBase\DEV\Core\OnBase.NET\WorkView\RestApis\Hyland.OnBase.WorkView.Core.WebApi\Controllers\FilterController.cs) - Filter operations and query execution
- [ClassController.cs](c:\OnBase\DEV\Core\OnBase.NET\WorkView\RestApis\Hyland.OnBase.WorkView.Core.WebApi\Controllers\ClassController.cs) - Class operations
- [ObjectController.cs](c:\OnBase\DEV\Core\OnBase.NET\WorkView\RestApis\Hyland.OnBase.WorkView.Core.WebApi\Controllers\ObjectController.cs) - CRUD operations on objects
- [AttributeController.cs](c:\OnBase\DEV\Core\OnBase.NET\WorkView\RestApis\Hyland.OnBase.WorkView.Core.WebApi\Controllers\AttributeController.cs) - Attribute operations

Supporting infrastructure (same project):
- `Authentication/AuthenticationSchemes.cs` — supported authentication schemes
- `Authorization/BearerTokenAuthorizeFilter.cs`, `HylandAuthorizationSetup.cs`, `OnBaseSessionCookieAuthorizeFilter.cs` — authorization pipeline
- `Filters/GlobalControllerExceptionAttribute.cs`, `InvalidModelStateFilter.cs`, `LogErrorResponseFilter.cs` — ASP.NET action filters
- `Exceptions/IWebApiException.cs`, `UnauthorizedSessionException.cs`, `UnauthorizedWorkViewAccessException.cs` — REST exception types
- `Services/IWorkViewSessionService.cs` / `WorkViewSessionService.cs` — REST-layer session management

#### B. HTTP/Web Controllers
Primary Integration: [WorkViewController](c:\OnBase\DEV\Core\OnBase.NET\Libraries\Hyland.Controls.Web\Workview\Handlers\WorkViewController.cs)

Methods include:
- GetApplications, InitializeApplication
- GetFilterBars
- CreateObject, DeleteObject, SaveObject
- ExecuteDynamicObjectQuery
- GetActionDisplayData

Used by: Web Client, External Access Client, Canvas applications

#### C. Unity SDK (Public API)
Location: [Libraries/Hyland.Unity/WorkView/](c:\OnBase\DEV\Core\OnBase.NET\Libraries\Hyland.Unity\WorkView)

Key Classes:
- Application.cs - Unity wrapper for applications
- Class.cs - Unity wrapper for classes
- Object.cs - Object operations
- CreateObject.cs, DeleteObject.cs - Object lifecycle
- ClientFilterQuery.cs - Filter query execution
- DynamicFilterQuery.cs - Dynamic filter creation
- Dataset.cs - Dataset access
- Action.cs - Action execution

Entry Point: `Hyland.Unity.Application.WorkView` property

#### D. Workflow Integration
Location: [Libraries/Hyland.Core.Workview/Workflow/](c:\OnBase\DEV\Core\OnBase.NET\Libraries\Hyland.Core.Workview\Workflow)

- **WorkviewWorkItemQuery** - Bridges Workflow Inbox with Workview filters
- **PortfolioQuery** - Manages portfolio relationships (parent-child object relations)

Workflow Actions:
- CreateWorkViewObjectFromDocument
- UpdateWorkViewObjectFromDocument
- ExecuteWorkviewAction
- Rule: WorkViewObjectExists

#### E. StatusView Integration
Location: [Libraries/Hyland.Applications.Web.StatusView/](c:\OnBase\DEV\Core\OnBase.NET\Libraries\Hyland.Applications.Web.StatusView)

Portlets:
- WorkviewFilterPortlet - Display filter results in dashboard
- WorkviewSummaryPortlet - Display summaries

Services:
- WorkviewFilterPortletService
- WorkviewSummaryPortletService

#### F. External Data Sources
- **WCF Services** - Service clients with proxy caching
- **ODBC External Sources** - Direct database querying with link servers
- **LOB/EIS Integration** - LOB data sources with XML serialization
- **Unity Scripts** - Custom scripting for filter logic and object operations

#### G. OnBase Studio Integration
Location: [Libraries/Hyland.Core.Studio.WorkView/](c:\OnBase\DEV\Core\OnBase.NET\Libraries\Hyland.Core.Studio.WorkView)

Studio is the **configuration tool** for WorkView — it does not participate in runtime request processing. It reads/writes configuration via `Hyland.Configuration.Repository.Items.WorkView.Components`.

Key source files:
- [WorkViewStudioUtils.cs](c:\OnBase\DEV\Core\OnBase.NET\Libraries\Hyland.Core.Studio.WorkView\WorkViewStudioUtils.cs) - Studio utility functions and operator helpers
- [WorkViewWFTrace.cs](c:\OnBase\DEV\Core\OnBase.NET\Libraries\Hyland.Core.Studio.WorkView\WorkViewWFTrace.cs) - Diagnostics and tracing for Studio operations
- [ImportExport/WorkViewImportXMLConverter.cs](c:\OnBase\DEV\Core\OnBase.NET\Libraries\Hyland.Core.Studio.WorkView\ImportExport\WorkViewImportXMLConverter.cs) - XML import/export for WorkView configuration
- [Dialogs/WorkViewDoctor.xaml](c:\OnBase\DEV\Core\OnBase.NET\Libraries\Hyland.Core.Studio.WorkView\Dialogs\WorkViewDoctor.xaml) - WorkView Doctor diagnostic tool (find/fix config issues)
- [OptionPages/WorkViewPage.xaml](c:\OnBase\DEV\Core\OnBase.NET\Libraries\Hyland.Core.Studio.WorkView\OptionPages\WorkViewPage.xaml) - Studio options page for WorkView settings

Studio UI Controls ([Controls/](c:\OnBase\DEV\Core\OnBase.NET\Libraries\Hyland.Core.Studio.WorkView\Controls)):
- `AttributeEditor.cs` - Attribute configuration UI
- `ApplicationPicker.cs`, `ClassPicker.cs` - Application/Class selection controls
- `StudioWorkViewFilterConfiguration.cs` - Filter configuration UI
- `StudioWorkViewConstraintSetConfiguration.cs` - Constraint set configuration
- `Designer/` - Visual screen designer (`WorkViewDesignTemplateBase.cs`, `DesignControlServer.cs`)
- `FlowDesigner/` - Action/operation flow designer
- `MobileDesigner/` - Mobile app template designer for WorkView mobile apps

Item Generator Wizards (in OnBase Studio app):
- [WorkViewTypeAndAmountPage.xaml](c:\OnBase\DEV\Core\OnBase.NET\Applications\Hyland.Applications.OnBase.Studio\ItemGenerator\Wizards\WorkViewTypeAndAmountPage.xaml)
- [WorkViewAttributesPage.xaml](c:\OnBase\DEV\Core\OnBase.NET\Applications\Hyland.Applications.OnBase.Studio\ItemGenerator\Wizards\WorkViewAttributesPage.xaml)

Studio Config Tests: [WorkView/Tests/Hyland.WorkView.Config.Tests.Common/](c:\OnBase\DEV\Core\OnBase.NET\WorkView\Tests\Hyland.WorkView.Config.Tests.Common)
- `StudioTestUtility.cs` - Utilities for testing Studio configurations
- `TestRepository.cs` - Test data / mock config repository

#### H. InterfaceServices (Unity Client / Canvas Desktop Client)
Location: [WorkView/Hyland.WorkView.InterfaceServices/](c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView.InterfaceServices)

**Important naming note:** The **Unity Client** is the OnBase desktop application (`Hyland.Canvas.Client`). It is entirely separate from the **Hyland Unity SDK** (`Libraries/Hyland.Unity/WorkView/`) which is the public developer API. Do not confuse them.

`Hyland.WorkView.InterfaceServices` is the **HTML rendering engine** that generates WorkView screens for the Unity (Canvas) desktop client. The Canvas client embeds an HTML view and InterfaceServices produces the HTML it displays.

Key source files:
- [WorkViewInterfaceServicesStartup.cs](c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView.InterfaceServices\WorkViewInterfaceServicesStartup.cs) - Service registration for InterfaceServices
- [LockObjectManager.cs](c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView.InterfaceServices\LockObjectManager.cs) - Object locking for concurrent editing sessions
- [DataSourceCache.cs](c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView.InterfaceServices\DataSourceCache.cs) - Data source caching layer
- [UserLayoutSettings.cs](c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView.InterfaceServices\UserLayoutSettings.cs) - Per-user layout persistence

ApplicationServerViewWriter ([ApplicationServerViewWriter/](c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView.InterfaceServices\ApplicationServerViewWriter)):

**Entry-point writers (final HTML emission):**
- `Writers/ScreenWriter.cs` + `ScreenWriterSettings.cs` — renders a complete **object detail screen** to HTML; uses `HTML/` controls + `RuleBuilder` to apply visibility rules ⭐
- `Writers/ViewWriter.cs` + `ViewWriterSettings.cs` — renders a **filter results view** (list/grid) to HTML; wires `RuleBuilder` and `Providers` into the output ⭐
- These are the last step in the Unity Client rendering pipeline before HTML is returned to the Canvas client

**Data contracts (Unity Client serialization shapes):**
- `DataContracts/` — Unity Client–side data contracts. **Parallel to `Libraries/Hyland.Controls.Web/Workview/Contracts/` for the Web Client.** Includes: `DisplayObject.cs`, `ApplicationInitData.cs`, `FilterBarItemData.cs`, `ActionConfigData.cs`, `ComponentData.cs`, `DeleteObjectResult.cs`, `DisplayCoordinates.cs`, `DisplayDataInformation.cs`, `LiveUpdate.cs`, `LiveUpdateField.cs`, `ObjectDataContract.cs`, `ObjectInstanceHistory.cs`, `FormData.cs`, `SimpleObject.cs`, and more ⭐

**HTTP service layer:**
- `HttpServices/HttpServiceProviderInitializer.cs` — initializes HTTP service providers for the Unity Client AppServer communication
- `HttpServices/SerializableWorkViewObject.cs` — serializable object wrapper for HTTP transport

**View item rendering utilities:**
- `ViewItemUtilities/ViewItemUtilities.cs` (and `.Attribute.cs`, `.CheckList.cs`, `.Filters.cs`, `.Folders.cs`, `.Screen.cs`) — multi-file partial class handling rendering of individual view items (attributes, filter lists, folder views, screen panels)

**HTML control hierarchy:**
- `Controls/IWorkViewControl.cs`, `IWorkViewScreen.cs` - Core screen/control interfaces
- `Controls/HTML/Base/` - Core HTML element builders (HTMLPage, HTMLBody, HTMLTable, etc.)
- `Controls/HTML/Components/` - UI components (Card, Panel, Toolbar, DataPresenter, etc.)
- `Controls/HTML/Buttons/` - Button rendering (toolbar, client-side action, lookup)
- `Controls/HTML/Inputs/` - Input control rendering (attribute inputs)
- `Controls/HTML/Labels/` - Label rendering (attribute labels, header labels)
- `Controls/HTML/Pages/` - Full page renderers (viewer page, print page, document header)
- `Controls/HTML/Screen/` - Screen-level controls (notification bar, view tabs, loading indicator)
- `Controls/HTML/View Items/` - View item controls (filter, folder, button, hyperlink)

---

### 1.4 Critical Files Map

#### Core Interface Definitions (Contracts)
```
WorkView/Hyland.WorkView/
├── IApplication.cs           - Application interface
├── IClass.cs                 - Class interface
├── IObject.cs                - Object interface
├── IFilter.cs                - Filter interface
├── IAttribute.cs             - Attribute interface
├── Actions/
│   └── IAction.cs            - Action interface
├── Services/                 ← SERVICE INTERFACE CONTRACTS (36 files)
│   ├── IObjectService.cs         - Object CRUD contract
│   ├── IFilterService.cs         - Filter operations contract
│   ├── IFilterQueryService.cs    - SQL execution contract
│   ├── IAttributeService.cs      - Attribute operations contract
│   ├── ICalendarViewService.cs   - Calendar contract
│   ├── IPermissionService.cs     - Permission evaluation contract
│   ├── IEquationManifestService.cs - Calculated attr contract
│   ├── ISequenceService.cs       - Sequence contract
│   └── ...all other IXxxService.cs
├── Providers/
│   ├── IApplicationProvider.cs
│   ├── IClassProvider.cs
│   ├── IFilterProvider.cs
│   ├── IObjectProvider.cs
│   └── IAttributeProvider.cs
├── Data/
│   ├── IDataAccessFacade.cs  - Central data access gateway
│   ├── IClassDataAccess.cs
│   ├── IFilterDataAccess.cs
│   └── IObjectDataAccess.cs
├── Internal/CalculatedAttributes/
│   ├── IEquationManifest.cs
│   ├── IEquationProcessor.cs
│   ├── ITransientResolver.cs
│   ├── IFunction.cs + IObjectFunction.cs + ISessionFunction.cs
│   └── INameValueMap.cs + INullableValue.cs
├── RuleBuilder/
│   ├── IRuleBuilderResolver.cs   - Rule resolution contract
│   └── JSONFiles/                - JSON rule definitions (9 files, one per operation type)
└── FilterQuery/
    ├── IFilterQuery.cs           - Core filter query contract
    ├── IClassFilterQuery.cs
    ├── IUnityScriptFilterQuery.cs
    └── DynamicQuery/             - IDynamicQueryBase, IDynamicClass, IDynamicColumn, etc.
```

#### Core Implementation
```
WorkView/Hyland.WorkView.Core/
├── Application.cs            - Application implementation
├── Class.cs                  - Class implementation
├── Object.cs                 - Object implementation (87KB - large)
├── Filter.cs                 - Filter implementation
├── Attribute.cs              - Attribute implementation
├── AttributeValue.cs         - Attribute values (97KB - comprehensive)
├── WorkViewStartup.cs        - Service registration ⭐
├── WorkViewUtility.cs        - Core utilities (145KB)
├── MacroStringManager.cs     - Macro expansion (90KB)
├── ModelBuilder.cs           - Model construction (108KB)
├── Services/                 - Business logic services
│   ├── ApplicationService.cs
│   ├── FilterService.cs
│   ├── ObjectService.cs
│   └── ...
└── Data/                     - Data access implementations
    ├── ObjectDataAccess.cs
    ├── MetadataListDataAccess.cs
    └── ...
```

#### Unity API (Public SDK)
```
Libraries/Hyland.Unity/WorkView/
├── Application.cs            - Unity wrapper
├── Class.cs                  - Unity wrapper
├── Object.cs                 - Unity wrapper
├── CreateObject.cs           - Object creation
├── DeleteObject.cs           - Object deletion
├── ClientFilterQuery.cs      - Filter queries
└── DynamicFilterQuery.cs     - Dynamic queries
```

#### REST APIs
```
WorkView/RestApis/Hyland.OnBase.WorkView.Core.WebApi/Controllers/
├── WVControllerBase.cs          - Base class for all WV controllers ⭐
├── ApplicationController.cs     - Application metadata
├── ClassController.cs           - Class/schema operations
├── ObjectController.cs          - CRUD operations on objects
├── FilterController.cs          - Filter execution and management
├── AttributeController.cs       - Attribute operations
├── CalendarController.cs        - Calendar view REST endpoints
├── DocumentController.cs        - Document attachment REST endpoints
├── DataSetController.cs         - DataSet query REST endpoints
├── FilterBarController.cs       - Filter bar REST endpoints
├── FilterBarItemController.cs   - Filter bar item REST endpoints
├── NotificationBarController.cs - Notification bar REST endpoints
├── SchemaController.cs          - Schema retrieval REST endpoints
├── UserSettingsController.cs    - Per-user settings REST endpoints
└── HealthCheckController.cs     - Health check endpoint

WorkView/RestApis/Hyland.OnBase.WorkView.Core.WebApi.Models/  ← ALL REST DTOs ⭐
├── ObjectModels/
│   ├── ObjectCreateModel.cs       - POST body for object creation
│   ├── ObjectUpdateModel.cs       - PUT body for object update
│   ├── ObjectResultModel.cs       - Response shape for object retrieval
│   ├── AbbreviatedObjectModel.cs  - Lightweight object reference in lists
│   └── ObjectQueryModel.cs        - Filter-based object query body
├── FilterModels/
│   ├── FilterModel.cs             - Filter metadata response
│   ├── FilterResultCollectionModel.cs - Filter execution result set
│   ├── FilterQueryModel.cs        - Filter query execution body
│   ├── ConstraintModel.cs         - Constraint definition
│   ├── ColumnModel.cs             - View column definition
│   ├── EntryConstraintModel.cs    - User-entry constraint
│   ├── FilterDynamicQueryModel.cs - Dynamic query body
│   └── FilterUserOverrideModel.cs - User filter override
├── CalendarModels/
│   ├── CalendarEventModel.cs      - Calendar event response
│   └── CalendarDisplayModeModel.cs
├── SchemaModels/
│   └── SchemaModel.cs / LegacyApplicationModel.cs
├── JsonConverters/
│   ├── FilterQueryModelJsonConverter.cs
│   └── JsonCreationConverter.cs
├── Enumerations/
│   ├── SortType.cs / wvOperator.cs / wvConnector.cs
└── Root models:
    AttributeModel.cs, AttributeValuesModel.cs, ClassModel.cs,
    DataSetModel.cs, FilterBarModel.cs, NotificationBarItemModel.cs,
    ValidationProblemModel.cs, ProblemModel.cs, HealthCheckModel.cs
```

#### Web Client & Handlers
```
Libraries/Hyland.Controls.Web/Workview/
├── Handlers/
│   └── WorkViewController.cs - Main HTTP handler ⭐
└── Contracts/               - Data contracts for serialization
```

#### Tests
```
WorkView/Tests/Hyland.WorkView.UnitTests/
└── TestBase.cs              - Test base class ⭐

WorkView/Tests/Hyland.WorkView.Config.Tests.Common/TestUtils/
├── StudioTestUtility.cs     - Studio configuration test helpers ⭐
└── TestRepository.cs        - Test config repository

WorkView/Tests/UIAutomation/
├── Hyland.WorkView.UI.PageObjects/  - UI Automation page objects
└── Hyland.WorkView.UI.Tests/        - End-to-end UI tests

Tests/Hyland.Core.Workview.Test/Core/
└── WorkviewUtilityTest.cs   - Utility tests

Tests/Hyland.Core.Unity.Test/Services/Workview/
├── WorkviewTest.Application.cs
├── WorkviewTest.Filters.cs
└── ...
```

#### OnBase Studio (Configuration Tool)
```
Libraries/Hyland.Core.Studio.WorkView/
├── WorkViewStudioUtils.cs         - Studio utility functions ⭐
├── WorkViewWFTrace.cs             - Diagnostics/tracing
├── ImportExport/
│   └── WorkViewImportXMLConverter.cs  - XML config import/export
├── Dialogs/
│   └── WorkViewDoctor.xaml        - WorkView Doctor tool ⭐
├── OptionPages/
│   └── WorkViewPage.xaml          - Studio options for WorkView
├── Controls/
│   ├── AttributeEditor.cs         - Attribute configuration UI
│   ├── ApplicationPicker.cs       - Application selection control
│   ├── ClassPicker.cs             - Class selection control
│   ├── StudioWorkViewFilterConfiguration.cs    - Filter config UI
│   ├── StudioWorkViewConstraintSetConfiguration.cs
│   ├── Designer/                  - Screen visual designer
│   ├── FlowDesigner/              - Action flow designer
│   └── MobileDesigner/            - Mobile app template designer
└── Commands/AddInCommands/
    └── DisplayWorkViewDoctorDlgCommand.cs

Applications/Hyland.Applications.OnBase.Studio/ItemGenerator/Wizards/
├── WorkViewTypeAndAmountPage.xaml  - Item generator wizard
└── WorkViewAttributesPage.xaml     - Attribute definition wizard
```

#### InterfaceServices (Unity Client / Canvas Desktop Client)
```
WorkView/Hyland.WorkView.InterfaceServices/
├── WorkViewInterfaceServicesStartup.cs  - Service registration ⭐
├── LockObjectManager.cs                 - Object locking
├── DataSourceCache.cs                   - Data source caching
├── UserLayoutSettings.cs                - User layout persistence
└── ApplicationServerViewWriter/
    ├── Controls/
    │   ├── IWorkViewControl.cs          - Control interface
    │   ├── IWorkViewScreen.cs           - Screen interface
    │   └── HTML/
    │       ├── Base/                    - Core HTML builders
    │       ├── Components/              - UI components
    │       ├── Buttons/                 - Button renderers
    │       ├── Inputs/                  - Input controls
    │       ├── Labels/                  - Label renderers
    │       ├── Pages/                   - Full page renderers
    │       ├── Screen/                  - Screen-level controls
    │       └── View Items/              - View item renderers
    └── WorkViewResource.cs              - Resource management
```

---

### 1.5 Four Client & Tool Perspectives

Understanding WorkView through these four lenses is essential — a change that looks correct in one perspective may break another.

---

#### Perspective 1: OnBase Studio (Configuration)

**What it is:** The WPF desktop configuration tool where administrators define WorkView applications, classes, attributes, filters, screens, actions, triggers, notifications, and mobile app templates.

**How it connects to code:**
- Studio reads/writes configuration via `Hyland.Configuration.Repository` — it does **not** call the runtime service stack
- Configuration changes made in Studio are persisted to the database and picked up by the App Server at runtime
- The `Hyland.Core.Studio.WorkView` library provides all the Studio UI controls and wizards
- Config is tested independently via `Hyland.WorkView.Config.Tests.Common`

**Key Studio-configured entities (not just runtime entities):**
- **Screens** — the visual layout of object detail views (configured in the Screen Designer)
- **Triggers** — event handlers that fire on object create/save/delete
- **Notifications** — alerts sent when object state changes
- **Filter Bars** — pre-configured filter sets for quick navigation
- **Mobile App Templates** — layouts for the WorkView mobile experience
- **DataSets** — external data source definitions
- **Lookup Lists** — pre-defined value lists for attributes

**Debugging Studio config issues:**
- Use **WorkView Doctor** (`Dialogs/WorkViewDoctor.xaml`) to diagnose configuration problems
- Config test suite: `WorkView/Tests/Hyland.WorkView.Config.UnitTests/` and `IntegrationTests/`
- If a runtime behavior doesn't match Studio config, check that the App Server has refreshed its config cache

---

#### Perspective 2: Web Client (Browser)

**What it is:** The ASP.NET web application that browser users interact with. WorkView in the Web Client is a set of ASPX pages served from `Applications/Hyland.Applications.Web.Client/Workview/`.

**How it connects to code:**
- The browser calls ASPX pages (e.g., `filterresults.aspx`, `objectPop.aspx`)
- ASPX pages delegate to `WorkViewController.cs` (the HTTP handler / SOA bridge)
- `WorkViewController` calls the service layer (`IFilterService`, `IObjectService`, etc.)
- Responses are serialized as JSON/XML for AJAX calls or rendered as HTML for page loads

**Key Web Client WorkView pages:**
```
Applications/Hyland.Applications.Web.Client/Workview/
├── filterresults.aspx          - Filter results grid view
├── filterPop.aspx              - Filter entry popup
├── objectPop.aspx              - Object detail popup
├── SelectApplication.aspx      - Application selection
├── SelectClass.aspx            - Class selection
├── CalendarView.aspx           - Calendar view
├── CalendarHandler.ashx        - Calendar AJAX handler
├── deleteObject.aspx           - Object deletion
├── doFilterAction.aspx         - Execute action on filter results
├── createEformFromObject.aspx  - Create eForm from object
├── createUnityFormFromObject.aspx  - Create Unity Form from object
├── viewDataSet.aspx            - DataSet viewer
├── viewObjectHistory.aspx      - Object history
├── ShareWorkViewFilter.aspx    - Filter sharing
├── ScreenBase.cs               - Base class for screen rendering
└── ObjectList.ascx             - Reusable object list control
```

**Web Client entry point:** [WorkViewController.cs](c:\OnBase\DEV\Core\OnBase.NET\Libraries\Hyland.Controls.Web\Workview\Handlers\WorkViewController.cs) — handles all SOA method calls from the browser.

**Key Web Client data contracts:** `Libraries/Hyland.Controls.Web/Workview/Contracts/` — the serialized request/response models.

**Web Client gotchas:**
- Session state lives in the HTTP session; stale session can cause unexpected behavior
- Calendar views use a separate ASHX handler (`CalendarHandler.ashx`), not WorkViewController
- Filter sharing (`ShareWorkViewFilter.aspx`) has its own permission model

---

#### Perspective 3: Unity Client / Canvas Client (Desktop)

**What it is:** The OnBase desktop application (`Hyland.Canvas.Client`). WorkView screens in the Unity Client are rendered as **HTML by the App Server** and displayed in an embedded browser panel inside the desktop app.

**CRITICAL naming distinction:**
| Term | Meaning |
|---|---|
| **Unity Client** / **Canvas Client** | The OnBase desktop application (`Hyland.Canvas.Client`) |
| **Hyland Unity SDK** / **Unity API** | The public developer API (`Libraries/Hyland.Unity/WorkView/`) used by customers and scripts |

These are completely different things. Do not confuse them.

**How it connects to code:**
- The Canvas client sends requests to the App Server
- The App Server uses `Hyland.WorkView.InterfaceServices` to generate HTML for WorkView screens
- `ApplicationServerViewWriter` builds the HTML using composable HTML control objects
- The Canvas client displays this HTML in an embedded web view

**Key code path for Unity Client WorkView:**
```
Canvas Client (desktop app)
    → App Server request
    → WorkViewInterfaceServicesStartup.cs (service registration)
    → ApplicationServerViewWriter/Controls/
        → IWorkViewScreen renders screen layout
        → HTML components compose the output
        → Returns HTML to Canvas client embedded browser
```

**Unity Client-specific files:**
```
WorkView/Hyland.WorkView.InterfaceServices/
├── WorkViewInterfaceServicesStartup.cs  - Service DI registration
├── LockObjectManager.cs                 - Concurrency / object locking
├── DataSourceCache.cs                   - Caching for data sources
├── UserLayoutSettings.cs                - Per-user column widths, etc.
└── ApplicationServerViewWriter/         - HTML rendering engine
```

**Unity Client gotchas:**
- WorkView screens are HTML-rendered server-side, not native WPF — display issues may be CSS/HTML rendering bugs in the ApplicationServerViewWriter
- Object locking (`LockObjectManager`) applies when a user opens an object for editing; lock issues only occur in the Unity Client path, not Web Client
- UI layout settings are stored per-user; corrupted layout settings can cause rendering problems

---

#### Perspective 4: App Server

**What it is:** The OnBase application server (`Applications/Hyland.Applications.Server/`) that hosts all WorkView runtime services. Both the Web Client and Unity Client ultimately call the App Server.

**What runs on the App Server:**
- The full service stack: services, providers, data access layer
- REST API controllers (`Hyland.OnBase.WorkView.Core.WebApi`)
- `WorkViewController` HTTP handler (for Web Client ASPX calls)
- `InterfaceServices` HTML rendering (for Unity Client)
- App Server WebAPI facets: `Hyland.WebApi.Facet.WorkViewFiles.dll`, `Hyland.WebApi.Facet.WorkViewDF.dll`

**App Server WorkView assembly set:**
```
Applications/Hyland.Applications.Server/bin64/
├── Hyland.WorkView.dll                  - Core interfaces
├── Hyland.WorkView.Core.dll             - Core implementation
├── Hyland.WorkView.Services.dll         - Service layer
├── Hyland.WorkView.Services.Core.dll    - Service implementations
├── Hyland.WorkView.InterfaceServices.dll - Unity Client rendering
├── Hyland.WorkView.Shared.dll           - Shared models
├── Hyland.WebApi.Facet.WorkViewFiles.dll - File-related WebAPI facet
└── Hyland.WebApi.Facet.WorkViewDF.dll   - Dynamic filter WebAPI facet
```

**App Server considerations:**
- Service registration: `WorkViewStartup.cs` and `WorkViewInterfaceServicesStartup.cs` both run at startup
- Config cache: The App Server caches WorkView configuration loaded from the database. A config change in Studio requires the App Server to refresh (recycle or explicit cache invalidation)
- Session lifecycle: WorkView objects are session-scoped; long-running sessions can hold stale data
- The App Server serves both the Web Client and the Unity Client — a bug may affect one or both depending on which code path it's in

---

### 1.6 Service Layer Map

All services are registered in [WorkViewStartup.cs](c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView.Core\WorkViewStartup.cs). InterfaceServices has its own registration in [WorkViewInterfaceServicesStartup.cs](c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView.InterfaceServices\WorkViewInterfaceServicesStartup.cs).

#### Object CRUD Services
| Service | File | Purpose |
|---|---|---|
| `ObjectService` | `Services/ObjectService/ObjectService.cs` | Coordinator — delegates to the 4 partial files below |
| `ObjectService.Create` | `Services/ObjectService/ObjectService.Create.cs` | Object creation logic |
| `ObjectService.Delete` | `Services/ObjectService/ObjectService.Delete.cs` | Object deletion + cascade |
| `ObjectService.Query` | `Services/ObjectService/ObjectService.Query.cs` | Object retrieval and search |
| `ObjectService.Update` | `Services/ObjectService/ObjectService.Update.cs` | Object save/update logic |
| `ObjectCacheService` | `Services/ObjectCacheService.cs` | Session-scoped object caching |
| `ObjectHistoryService` | `Services/ObjectHistoryService.cs` | History record retrieval |
| `ObjectDocumentService` | `Services/ObjectDocumentService.cs` | Document attachment operations |
| `ObjectSyncService` | `Services/ObjectSyncService.cs` | Multi-server sync operations |

#### Filter & Query Services
| Service | File | Purpose |
|---|---|---|
| `FilterService` | `Services/FilterService.cs` | Filter metadata and execution orchestration |
| `FilterQueryService` | `Services/FilterQueryService.cs` | SQL query generation and execution |
| `FilterCopyService` | `Services/FilterCopyService.cs` | Filter copy/share operations |
| `FilterBarItemService` | `Services/FilterBarItemService.cs` | Filter bar item operations |
| `FullTextFilterEntryAttributeService` | `Services/FullTextFilterEntryAttributeService.cs` | Full-text search entry handling |
| `ItemListEntryService` | `Services/ItemListEntryService.cs` | Item list (lookup) entry operations |

#### Attribute Services
| Service | File | Purpose |
|---|---|---|
| `AttributeService` | `Services/AttributeService.cs` | Attribute metadata operations |
| `AttributeEncryptionService` | `Services/AttributeEncryptionService.cs` | Encrypted attribute encrypt/decrypt |
| `CompositeKeyService` | `Services/CompositeKeyService.cs` | Composite key generation and validation |
| `DataSetValueService` | `Services/DataSetValueService.cs` | DataSet query execution and value retrieval |
| `EquationManifestService` | `Services/EquationManifestService.cs` | Calculated attribute equation processing |
| `SequenceService` | `Services/SequenceService.cs` | Auto-increment sequence generation |

#### Application & Class Services
| Service | File | Purpose |
|---|---|---|
| `ApplicationService` | `Services/ApplicationService.cs` | Application metadata and initialization |
| `DTLoadClassDataSourceService` | `Services/DTLoadClassDataSourceService.cs` | DataTable-based class data source loading |
| `MobileComponentService` | `Services/MobileComponentService.cs` | Mobile app component operations |
| `DisplayTemplateService` | `Services/DisplayTemplateService.cs` | Display template resolution |
| `ViewDataService` | `Services/ViewDataService.cs` | View data retrieval |
| `CalendarViewService` | `Services/CalendarViewService.cs` | Calendar view and event data |

#### Notification & Event Services
| Service | File | Purpose |
|---|---|---|
| `NotificationService` | `Services/NotificationService.cs` | Notification rule processing |
| `UserDefinedNotificationService` | `Services/UserDefinedNotificationService.cs` | User-defined alert subscriptions |
| `EventLogService` | `Services/EventLogService.cs` | Event log retrieval |

#### Security & User Services
| Service | File | Purpose |
|---|---|---|
| `PermissionService` | `Services/PermissionService.cs` | Object/class permission evaluation |
| `SecurityResolver` | `Services/SecurityResolver.cs` | Security context resolution |
| `UserSecurityService` | `Services/UserSecurityService.cs` | User-level security checks |
| `UserSettingsService` | `Services/UserSettingsService.cs` | Per-user settings persistence |
| `SessionStateService` | `Services/SessionStateService.cs` | Session state management |
| `InstitutionService` | `Services/InstitutionService.cs` | Multi-institution support |

#### Infrastructure Services
| Service | File | Purpose |
|---|---|---|
| `UnityScriptService` | `Services/UnityScriptService.cs` | Unity Script execution engine |
| `ResourceService` | `Services/ResourceService.cs` | Embedded resource access |
| `DescriptionService` | `Services/DescriptionService.cs` | Object/class description generation |
| `ExternalDataManagerService` | `Services/ExternalDataManagerService.cs` | External data source management |
| `OutlookService` | `Services/OutlookService.cs` | Outlook/email integration |
| `SystemPropertyService` | `Services/SystemPropertyService.cs` | System property read/write |

---

### 1.7 External Data Access Variants

WorkView has **six distinct data access implementations** for objects, selected based on the filter/object type. This is one of the most important things to know when debugging data issues — the wrong path can cause silent failures.

Location: [WorkView/Hyland.WorkView.Core/Data/](c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView.Core\Data)

The factory that selects the correct implementation: [ObjectDataAccessFactory.cs](c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView.Core\ObjectDataAccessFactory.cs)

| Implementation | File | When Used |
|---|---|---|
| `StandardObjectDataAccess` | `Data/StandardObjectDataAccess.cs` | Normal WorkView objects stored in the OnBase database |
| `ExternalLinkedServerObjectDataAccess` | `Data/ExternalLinkedServerObjectDataAccess.cs` | ODBC objects via SQL linked server |
| `ExternalODBCObjectDataAccess` | `Data/ExternalODBCObjectDataAccess.cs` | ODBC objects via direct connection |
| `ExternalWCFObjectDataAccess` | `Data/ExternalWCFObjectDataAccess.cs` | Objects sourced from a WCF service |
| `ExternalLOBBrokerObjectDataAccess` | `Data/ExternalLOBBrokerObjectDataAccess.cs` | LOB/EIS broker-sourced objects |
| `ExternalUnityScriptObjectDataAccess` | `Data/ExternalUnityScriptObjectDataAccess.cs` | Unity Script-sourced objects |

All inherit from [DataAccessBase.cs](c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView.Core\Data\DataAccessBase.cs).

**Other key data access files:**

| File | Purpose |
|---|---|
| `ObjectQueryBuilder.cs` | Builds the SQL queries for filter execution — go here for SQL generation bugs |
| `QueryDataAccess.cs` | Executes the built queries against the database |
| `ObjectDataAccess.cs` | Core object data operations (read/write to rmobjectinstance tables) |
| `MetadataListDataAccess.cs` | Metadata list operations (lookup lists, key types) |
| `WVAuditHelper.cs` | Writes audit records for object changes |
| `WVAdminOperationsDataAccess.cs` | Admin-level bulk operations (mass update, transfer) |
| `TransferDataAccess.cs` | Object transfer between systems/applications |
| `OutlookDataAccess.cs` | Outlook metadata data access |
| `WorkViewDBUtils.cs` | Common DB utility functions |

**⚠️ Debugging tip:** When a filter returns unexpected results or no results for external-source objects, check which `ExternalXxxObjectDataAccess` is being selected by `ObjectDataAccessFactory`. Each implementation has different SQL generation and connection handling.

---

### 1.8 Key Large Files & Subsystems

These files are large, complex, and touched frequently. Know they exist before diving in.

#### WorkViewUtility.cs (145KB)
[WorkView/Hyland.WorkView.Core/WorkViewUtility.cs](c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView.Core\WorkViewUtility.cs)

The core utility class — a large collection of shared helper methods used throughout the codebase. If you're debugging a bug where something is being formatted, parsed, or validated unexpectedly, there's a good chance the logic is in here. Check this file before writing new utility code.

#### MacroStringManager.cs (90KB)
[WorkView/Hyland.WorkView.Core/MacroStringManager.cs](c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView.Core\MacroStringManager.cs)

Handles **macro expansion** throughout WorkView — macros appear in filter constraints, object names, display templates, notification messages, and action command strings. If a displayed value or constraint value looks wrong and contains `{...}` syntax, the bug is likely in here.

Common macro patterns: `{AttributeName}`, `{CurrentUser}`, `{CurrentDate}`, `{ObjectID}`.

#### ModelBuilder.cs (108KB)
[WorkView/Hyland.WorkView.Core/ModelBuilder.cs](c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView.Core\ModelBuilder.cs)

Constructs the data models (`DisplayObject`, `ObjectModel`, `FilterResultsModel`) that are serialized and sent to clients. If a field appears in the database but is missing in the client response, this is the first place to look. It maps provider data → serializable contracts.

#### AttributeValue.cs (97KB)
[WorkView/Hyland.WorkView.Core/AttributeValue.cs](c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView.Core\AttributeValue.cs)

One of the most-touched files. Handles all attribute value read/write operations including type coercion, validation, encrypted values, calculated fields, and composite keys. Nearly every object save/load path goes through here.

#### Object.cs (87KB)
[WorkView/Hyland.WorkView.Core/Object.cs](c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView.Core\Object.cs)

The core object implementation. Security checks, attribute access, document operations, history, event logs, and object state management all live in here.

#### Web Client Data Contracts
[Libraries/Hyland.Controls.Web/Workview/Contracts/](c:\OnBase\DEV\Core\OnBase.NET\Libraries\Hyland.Controls.Web\Workview\Contracts)

JSON/XML contracts serialized between the Web Client browser and WorkViewController. If a field is visible in the DB but missing in the browser, the issue is often in these contracts or in how `ModelBuilder` populates them.

Key contracts:
- `DisplayObject.cs` — primary object detail contract sent to the browser
- `ApplicationInitData.cs` — initialization data sent when an application loads
- `FilterBarItemData.cs` — filter bar rendering data
- `DisplayDataInformation.cs` / `DisplayCoordinates.cs` — action display output
- `FormData.cs` — eForm/Unity Form data
- `SimpleObject.cs` — lightweight object reference

#### Hyland Unity SDK — WCF Formatter Layer
[Libraries/Hyland.Unity/WorkView/Services/HylandServices/](c:\OnBase\DEV\Core\OnBase.NET\Libraries\Hyland.Unity\WorkView\Services\HylandServices)

The Unity SDK communicates with the App Server via WCF/SOAP. This subdirectory contains serializers and deserializers — one per entity type. If SDK data is missing fields after a server call, check the relevant deserializer.

Key files:
- `WorkViewServiceAccess.cs` — WCF service proxy entry point
- `ServiceMethods.cs` — WCF method name constants
- `Formatters/Deserializers/` — ~25 deserializer files (one per entity)
- `Formatters/Serializers/` — ~8 serializer files

#### LiveUpdateDisplayTemplateService
[WorkView/Hyland.WorkView.InterfaceServices/ApplicationServerViewWriter/LiveUpdateDisplayTemplateService.cs](c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView.InterfaceServices\ApplicationServerViewWriter\LiveUpdateDisplayTemplateService.cs)

Handles live-updating display templates in the Unity Client — when an object changes, the template re-renders without a full page reload. Issues with stale display in the Unity Client often trace here.

---

### 1.9 Internal Subsystems & Supporting Infrastructure

These subsystems exist in the codebase but are not part of the main runtime request flow described in 1.1–1.8. Knowing they exist prevents wasted debugging time searching in the wrong layer.

---

#### 1.9.1 Per-Entity Data Access Layer (`Core/DataAccess/`)

Location: [WorkView/Hyland.WorkView.Core/DataAccess/](c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView.Core\DataAccess)

**This is a different directory from `Data/`** (which holds query objects and the 6 external access variants). `DataAccess/` holds **55+ individual data access classes** — one per domain entity — that handle direct DB reads/writes for configuration and metadata.

Interface contracts in: [WorkView/Hyland.WorkView/Data/](c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView\Data) (all `IXxxDataAccess.cs` files)

Key files:

| File | Entity |
|---|---|
| `AttributeDataAccess.cs` | Attribute metadata DB operations |
| `ClassDataAccess.cs` | Class definition DB operations |
| `FilterDataAccess.cs` | Filter definition DB operations |
| `CalendarViewDataAccess.cs` | Calendar view metadata |
| `ClassTriggerDataAccess.cs` / `ClassTriggerBucketDataAccess.cs` | Trigger metadata |
| `ConstraintDataAccess.cs` / `ConstraintSetDataAccess.cs` | Constraint storage |
| `DataSetDataAccess.cs` / `DataSetValueDataAccess.cs` | DataSet metadata |
| `EncryptedAlphanumericDataAccess.cs` | Encrypted attribute storage |
| `FilterCopyDataAccess.cs` | Filter copy/share persistence |
| `MobileAppPageDataAccess.cs` / `MobileFabDataAccess.cs` | Mobile app config |
| `NotificationDataAccess.cs` | Notification rule storage |
| `ObjectDocumentDataAccess.cs` | Document attachment metadata |
| `ObjectHeaderFooterDataAccess.cs` / `ObjectHeaderFooterCellDataAccess.cs` | Header/footer config |
| `ObjectHistoryDataAccess.cs` | History record retrieval |
| `ObjectSyncDataAccess.cs` | Sync state records |
| `SequenceDataAccess.cs` | Sequence counter DB operations |
| `UnityScriptDataAccess.cs` | Unity Script metadata |
| `UserDefinedNotificationDataAccess.cs` | User notification subscriptions |
| `UserObjectDataAccess.cs` | User-object associations |
| `UserSettingsDataAccess.cs` | Per-user settings |
| `WorkflowUtilityDataAccess.cs` | Workflow integration data access |

**When to look here:** If a service or provider is loading/saving metadata (class definitions, filter config, attribute settings) and the data is wrong, the bug is likely in one of these files — not in `Data/ObjectQueryBuilder.cs` which is only for filter execution.

---

#### 1.9.2 Facade / Access Aggregation Layer (`Core/Facades/`)

Location: [WorkView/Hyland.WorkView.Core/Facades/](c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView.Core\Facades)

The facades layer aggregates access to providers, services, and data access into single-entry-point objects. Code throughout the system uses these rather than injecting every dependency individually.

| File | Purpose |
|---|---|
| `CoreProviderAccess.cs` | Aggregates all core provider interfaces into one access object |
| `DataAccessFacade.cs` | Aggregates all `IXxxDataAccess` implementations — single gateway to the DA layer |
| `DataObjectHelper.cs` | Helper for working with data objects; handles common pattern boilerplate |
| `LegacyCoreAccess.cs` | Backward-compatible access for legacy code paths |
| `ObjectFacade.cs` | Aggregates object-related operations (CRUD, history, documents) |
| `ProviderAccess.cs` | General provider aggregator used by services |
| `ServiceAccess.cs` | Aggregates all service interfaces — single entry point into the service layer |
| `WorkflowProviderAccess.cs` | Workflow-specific provider aggregation |

**When to look here:** If you're adding a new service and other services can't find it, check that it's accessible via `ServiceAccess.cs` and `DataAccessFacade.cs`. Also check here when tracing how a deep call actually resolves to a specific provider or data access.

---

#### 1.9.3 Calculated Attributes Deep Chain (`Core/CalculatedAttributes/`)

Location: [WorkView/Hyland.WorkView.Core/CalculatedAttributes/](c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView.Core\CalculatedAttributes)

`EquationManifestService.cs` (in Services/) is the entry point — but the real processing chain lives in `CalculatedAttributes/`:

```
EquationManifestService
    → EquationManifest.cs          - Parses the equation definition
    → EquationProcessor.cs         - Evaluates the equation
    → Function.cs + Functions/     - Built-in function implementations
    → TransientResolver.cs         - Resolves transient (dynamic) source values
    → TransientResolverFactory.cs  - Creates the right resolver per source type
    → TransientInfo.cs             - Metadata about a transient value source
    → TransientSourceAddress.cs    - Address of a transient data source
    → NullableValue.cs             - Wraps potentially-null values during evaluation
    → UnresolvedValue.cs           - Sentinel for values that couldn't be resolved
    → NameValueMap.cs              - Maps names → values during evaluation
```

**When to look here:** Calculated attribute values that are wrong, null, or throw exceptions. The equation evaluator is here, not in `AttributeValue.cs`.

---

#### 1.9.4 Server-Side Doctor / Maintenance Framework (`Core/Doctor/`)

Location: [WorkView/Hyland.WorkView.Core/Doctor/](c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView.Core\Doctor)

**This is NOT the Studio WorkView Doctor dialog** (`Libraries/Hyland.Core.Studio.WorkView/Dialogs/WorkViewDoctor.xaml`). That is the Studio UI tool. This is the **server-side diagnostic and maintenance framework** that the Studio Doctor UI calls into.

| File/Dir | Purpose |
|---|---|
| `IWorkViewDoctorTest.cs` | Interface for a single diagnostic test |
| `IWorkViewDoctorSubTest.cs` | Interface for a sub-test within a diagnostic |
| `IWorkViewTestRunner.cs` | Interface for the test runner |
| `TestRunner.cs` | Executes all registered diagnostic tests |
| `WorkViewDoctorTestBase.cs` | Base class for implementing doctor tests |
| `WorkViewDoctorTestAttribute.cs` | Attribute to mark a class as a doctor test |
| `DataObjectUpdater/` | Utilities for bulk-updating data objects during maintenance |
| `Maintenance/` | Maintenance operation implementations |
| `ViewUpdater/` | View/screen migration utilities |

**When to look here:** Implementing a new WorkView Doctor test, debugging why a doctor test reports false positives/negatives, or building a maintenance/migration operation.

---

#### 1.9.5 Solutions / Component Management (`Core/Solutions/`)

Location: [WorkView/Hyland.WorkView.Core/Solutions/](c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView.Core\Solutions)

Manages WorkView **screen components** — reusable UI components embedded within screens, including legacy V2 UI components, component scripts, and component styles.

| File | Purpose |
|---|---|
| `Component.cs` | Core component entity |
| `ComponentFactory.cs` | Creates components by type |
| `ComponentManager.cs` | Orchestrates component lifecycle |
| `ComponentManager.Screen.cs` | Screen-specific component management |
| `ComponentManager.Template.cs` | Template-specific component management |
| `ComponentManager.View.cs` | View-specific component management |
| `ComponentScript.cs` / `ComponentScriptDataAccess.cs` | Script attached to a component |
| `ComponentStyle.cs` / `ComponentStyleDataAccess.cs` | CSS styling for a component |
| `Screen.cs` / `ScreenComponent.cs` | Screen and its components |
| `LegacyV2UIComponents/` | Backward-compatible V2 UI component implementations |

**When to look here:** Screen rendering issues in either client when components are involved; component script execution bugs; migrating V2 components.

---

#### 1.9.6 Report Processor (`Core/ReportProcessor/`)

Location: [WorkView/Hyland.WorkView.Core/ReportProcessor/](c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView.Core\ReportProcessor)

Handles **WorkView report generation** — the output from `IDisplayData` / `IReportProcessor` actions. Produces HTML, PDF, and other formatted outputs from filter results via a template engine.

| File | Purpose |
|---|---|
| `ReportProcessor.cs` | Main orchestrator — processes a report action |
| `TemplateGenerator.cs` | Generates output from a report template |
| `HTMLGenerator.cs` | HTML output generation |
| `TemplateBlock.cs` / `IterativeTemplateBlock.cs` | Template block model (iterates over result rows) |
| `AggregateNameValueMap.cs` / `AggregateVariable.cs` | Aggregate (sum/count/avg) variables in templates |
| `ControlBreakSet.cs` | Control break groupings in report output |
| `FilterUtility.cs` | Filter-to-report data bridging |
| `FormatConverter.cs` | Value formatting for report output |
| `IFilterRuntime.cs` | Interface for filter runtime during report processing |
| `BlockProcessors/` | Specialized block processors for different output types |
| `OutputFormats/` | Output format implementations (HTML, etc.) |

**When to look here:** Bugs in WorkView report output (wrong values, missing rows, formatting issues, broken templates).

---

#### 1.9.7 XML Data Mapping & Transfer (`Core/XmlDataMapping/`, `Core/Transfer/`)

**XmlDataMapping** — Location: [WorkView/Hyland.WorkView.Core/XmlDataMapping/](c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView.Core\XmlDataMapping)

Implements the data managers for XML-based external sources (LOB/EIS and WCF sources). Used by `ExternalWCFObjectDataAccess` and `ExternalLOBBrokerObjectDataAccess`.

| File | Purpose |
|---|---|
| `LobBrokerDataManager.cs` | LOB/EIS XML data management |
| `LobXmlFormatProvider.cs` | LOB XML format provider |
| `WcfDataManager.cs` / `WcfDataManagerFactory.cs` | WCF service data manager |
| `WcfServiceProperties.cs` | WCF connection properties |
| `XmlDataManagerBase.cs` | Base class for XML data managers |
| `DummyXmlFormatProvider.cs` | Test/fallback format provider |
| `PostProcessedXmlObject.cs` | Post-processed XML object model |

**Transfer** — Location: [WorkView/Hyland.WorkView.Core/Transfer/](c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView.Core\Transfer)

Handles **attribute value mapping and transfer** — mapping WorkView attribute values to/from other systems (Workflow, document keywords, XML) using a resolver chain.

| File | Purpose |
|---|---|
| `AttributeMapping.cs` / `AttributeMappingResolverFactory.cs` | Maps attributes between contexts |
| `AttributeContextMapping.cs` / `AttributeContextMappingResolver.cs` | Context-based attribute mapping |
| `AttributeValueMapping.cs` / `AttributeValueMappingResolver.cs` | Value-level mapping resolution |
| `AttributeLookupMapping.cs` / `AttributeLookupMappingResolver.cs` | Lookup list value mapping |
| `AttributeXPathMapping.cs` / `AttributeXPathMappingResolver.cs` | XPath-based XML value mapping |
| `AttributeDataAddressMapping.cs` | Data address mapping |
| `AttributeMappingUtility.cs` | Shared mapping utilities |
| `ExportManager.cs` | Export orchestration |
| `FilterResultInstanceMapping.cs` | Maps filter result rows to target entities |

**When to look here:** Bugs in action data transfer (Workflow → WorkView, WorkView → document keywords), XML source object field mapping, LOB/WCF data not populating correctly.

---

#### 1.9.8 InterfaceServices: RuleBuilder, QueryResultWriters, Services, Interfaces/Providers

**RuleBuilder** — [WorkView/Hyland.WorkView.InterfaceServices/RuleBuilder/](c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView.InterfaceServices\RuleBuilder)

Evaluates screen visibility and field-state rules in the Unity Client. Each operation type resolver applies a specific rule (field visibility, readonly, required, filter visibility, folder visibility, etc.).

| File | Purpose |
|---|---|
| `RuleBuilderResolver.cs` | Entry point — resolves which operation type resolver to use |
| `IOperationTypeResolver.cs` | Interface all resolvers implement |
| `OperationTypes/CompareFieldValueResolver.cs` | Rule: compare a field value |
| `OperationTypes/ConstrainFilterResolver.cs` | Rule: constrain a filter |
| `OperationTypes/IsUserInUserGroupResolver.cs` | Rule: check user group membership |
| `OperationTypes/SetActionButtonStateResolver.cs` | Rule: show/hide action buttons |
| `OperationTypes/SetFieldVisibilityResolver.cs` | Rule: show/hide fields |
| `OperationTypes/SetFilterVisibilityResolver.cs` | Rule: show/hide filter bars |
| `OperationTypes/SetFolderVisibilityResolver.cs` | Rule: show/hide doc folders |
| `OperationTypes/SetReadonlyResolver.cs` | Rule: make fields read-only |
| `OperationTypes/SetRequiredResolver.cs` | Rule: make fields required |

**When to look here:** Unity Client screen rules not firing, wrong fields hidden/shown, action buttons appearing when they shouldn't.

**QueryResultWriters** — [WorkView/Hyland.WorkView.InterfaceServices/QueryResultWriters/](c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView.InterfaceServices\QueryResultWriters)

Serialize filter results for the Unity Client.

| File | Purpose |
|---|---|
| `JsonQueryResultWriter.cs` | Standard JSON result writer |
| `JsonQueryResultWriter2.cs` | Second-generation JSON writer (newer format) |
| `ElementListQueryResultWriter.cs` | Element list format output |
| `JsonFolderResultWriter.cs` | Folder view result output |
| `QueryResultWriter.cs` | Base/abstract result writer |

**When to look here:** Unity Client filter results missing fields, wrong format, or serialization errors.

**InterfaceServices Services** — [WorkView/Hyland.WorkView.InterfaceServices/Services/](c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView.InterfaceServices\Services)

Unity Client–specific services (registered in `WorkViewInterfaceServicesStartup.cs`):

| Service | Purpose |
|---|---|
| `ClassUserPositionService.cs` | Tracks user's last-opened class position |
| `ObjectTitleService.cs` | Resolves display titles for objects in the Unity Client |
| `UserLayoutSettingsService.cs` | Persists column widths and panel layout per user |
| `UserUISettingService.cs` | General per-user UI settings |
| `ItemListEntryService.cs` | Item list (lookup list) entry operations for Unity Client |

**InterfaceServices Interfaces & Providers** — [WorkView/Hyland.WorkView.InterfaceServices/Interfaces/](c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView.InterfaceServices\Interfaces) and [Providers/](c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView.InterfaceServices\Providers)

Declares and implements interfaces used exclusively by the Unity Client path:
- `IClassUserPosition` / `IClassXFolder` / `IItemListEntry` / `IUserUISetting` / `IDataSourceCache`
- Corresponding `*Provider.cs` implementations in `Providers/`

---

#### 1.9.9 Shared Models (`Hyland.WorkView.Shared/`)

Location: [WorkView/Hyland.WorkView.Shared/](c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView.Shared)

A small shared assembly that holds models and attributes used across multiple WorkView assemblies (avoids circular dependencies).

| File | Purpose |
|---|---|
| `Models/CompositeKeyModel.cs` | Shared composite key model used by SDK and Core |
| `Models/LookaheadConstraintResultListModel.cs` | Lookahead constraint result list model |
| `Models/LookaheadConstraintResultModel.cs` | Single lookahead constraint result |
| `Attributes/LocalizationAttribute.cs` | Attribute for marking localizable properties |

Also contains: `Hyland.WorkView.ComponentDoctor` solution (standalone diagnostic WPF app — see 1.9.12).

---

#### 1.9.10 Internal Utility Subsystems

These directories hold internal infrastructure used throughout the Core layer. They are not exposed externally.

**Guards** — [WorkView/Hyland.WorkView.Core/Guards/](c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView.Core\Guards)
- `WVGuard.cs` — Argument/precondition guard methods (null checks, range checks). Used by services instead of raw `throw`.

**Options** — [WorkView/Hyland.WorkView.Core/Options/](c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView.Core\Options)
- `WorkViewOptions.cs` — ASP.NET Options pattern configuration for WorkView feature flags and settings.
- `ConfigureDefaultWorkViewOptions.cs` — Default configuration setup.

**Query** — [WorkView/Hyland.WorkView.Core/Query/](c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView.Core\Query)
- A structured query model (`IQueryInfoTree`, `IQueryItem`, `IQueryJoin`, etc.) that represents a SQL query abstractly before rendering to SQL via `SQLString/`.
- `QueryCache.cs` / `IQueryCache.cs` — Caches built query structures.
- `QueryItemService.cs` — Builds query item trees.

**SQLString** — [WorkView/Hyland.WorkView.Core/SQLString/](c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView.Core\SQLString)
- `SQLString.cs` — Builds raw SQL strings from `Query/` model objects.
- `SelectColumn.cs` / `SelectColumnList.cs` — Column selection building blocks.

**SecurityAttributes** — [WorkView/Hyland.WorkView.Core/SecurityAttributes/](c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView.Core\SecurityAttributes)
- `FilterQuerySecurityAttributeResolver.cs` — Resolves security attributes on filter queries.
- `SecurityAttributeResolver.cs` — Core security attribute resolution.
- `SecurityAttributeResolver.UserIdentityResolver.cs` — User identity part of security resolution.

**Validation** — [WorkView/Hyland.WorkView.Core/Validation/](c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView.Core\Validation)
- `WVTemplateMigrationValidator.cs` — Validates screen/template migrations during upgrades.

---

#### 1.9.11 Interface WebAPI Variant

Location: [WorkView/RestApis/Hyland.OnBase.WorkView.Interface.WebApi.UnitTests/](c:\OnBase\DEV\Core\OnBase.NET\WorkView\Tests\Hyland.OnBase.WorkView.Interface.WebApi.UnitTests) (tests only visible in `Tests/` directory)

There are **two WebAPI variants**:
- `Hyland.OnBase.WorkView.Core.WebApi` — The primary REST API (15 controllers) described in Section 1.3A. Used by all modern REST consumers.
- `Hyland.OnBase.WorkView.Interface.WebApi` — An interface/contract-only variant. Hosts the same operations but through a different endpoint or contract surface. Has separate unit tests.

**When to check:** If a REST API change works in one endpoint but not the other, or if you're unsure which endpoint a client is calling, check which WebApi assembly is in use.

---

#### 1.9.12 ComponentDoctor Utility

Location: [WorkView/Utilities/Hyland.WorkView.ComponentDoctor/](c:\OnBase\DEV\Core\OnBase.NET\WorkView\Utilities\Hyland.WorkView.ComponentDoctor)

A **standalone WPF diagnostic application** (separate from OnBase Studio) for diagnosing and repairing WorkView component data in a database. Used by support/engineering for data repair operations.

Structure:
- `MainWindow.xaml` / `MainWindow.xaml.cs` — Primary UI
- `ViewModels/` — MVVM view models
- `Models/` — Data models for diagnostic results
- `PasswordPromptDialog.xaml` — DB connection prompt

**When to use:** Database-level WorkView component data is corrupt or inconsistent and cannot be fixed via the Studio WorkView Doctor UI (which operates at config level, not data level).

---

#### 1.9.13 Complete Test Project Inventory

Location: [WorkView/Tests/](c:\OnBase\DEV\Core\OnBase.NET\WorkView\Tests)

| Project | Type | What it Tests |
|---|---|---|
| `Hyland.WorkView.UnitTests/` | Unit tests | Core domain logic — all main entity tests (`AttributeTest.cs`, `ClassTest.cs`, `CalendarViewTest.cs`, etc.) |
| `Hyland.WorkView.Test.Framework/` | Test framework | Shared test infrastructure — `TestUtility.cs`, `TestConstants.cs`, `Builders/`, `Arrangement/`, `Assertion/` |
| `Hyland.WorkView.IntegrationTests/` | Integration tests | End-to-end Core stack integration — requires a live DB |
| `Hyland.WorkView.IntegrationTests.Shared/` | Shared infra | Shared helpers and fixtures for integration tests |
| `Hyland.WorkView.Config.UnitTests/` | Unit tests | Studio configuration logic unit tests |
| `Hyland.WorkView.Config.IntegrationTests/` | Integration tests | Studio config integration — requires live DB |
| `Hyland.WorkView.Config.Tests.Common/` | Shared infra | `StudioTestUtility.cs`, `TestRepository.cs` — used by both Config test projects |
| `Hyland.WorkView.InterfaceServices.IntegrationTests/` | Integration tests | Unity Client HTML rendering integration tests |
| `Hyland.OnBase.WorkView.Core.WebApi.UnitTests/` | Unit tests | REST API controller unit tests (Core WebApi) |
| `Hyland.OnBase.WorkView.Interface.WebApi.UnitTests/` | Unit tests | REST API controller unit tests (Interface WebApi variant) |
| `Hyland.OnBase.WorkView.RestApi.IntegrationTests/` | Integration tests | REST API end-to-end integration tests |
| `UIAutomation/Hyland.WorkView.UI.PageObjects/` | UI automation | Page object model for UI test framework |
| `UIAutomation/Hyland.WorkView.UI.Tests/` | UI automation | End-to-end browser/desktop UI tests |

**Test base classes:**
- `WorkView/Tests/Hyland.WorkView.UnitTests/TestBase.cs` — Base for all Core unit tests
- `WorkView/Tests/Hyland.WorkView.Test.Framework/TestUtility.cs` — Shared test utility
- `WorkView/Tests/Hyland.WorkView.Config.Tests.Common/TestUtils/StudioTestUtility.cs` — Studio config test base

---

#### 1.9.14 REST API Models Project (`Hyland.OnBase.WorkView.Core.WebApi.Models/`)

Location: [WorkView/RestApis/Hyland.OnBase.WorkView.Core.WebApi.Models/](c:\OnBase\DEV\Core\OnBase.NET\WorkView\RestApis\Hyland.OnBase.WorkView.Core.WebApi.Models)

A **separate project** from the controllers project that holds every REST request/response DTO. Any REST API change that adds, renames, or restructures a field must be made here as well as in the controller. The project is also referenced by integration test projects.

**Key DTO groups:**

| Folder | DTOs | When you need them |
|---|---|---|
| `ObjectModels/` | `ObjectCreateModel`, `ObjectUpdateModel`, `ObjectResultModel`, `AbbreviatedObjectModel`, `ObjectQueryModel` | Any object CRUD endpoint change |
| `FilterModels/` | `FilterModel`, `FilterResultCollectionModel`, `FilterQueryModel`, `ConstraintModel`, `ColumnModel`, `FilterDynamicQueryModel`, `FilterUserOverrideModel` | Any filter execution or query endpoint change |
| `CalendarModels/` | `CalendarEventModel`, `CalendarDisplayModeModel` | Calendar endpoint changes |
| `SchemaModels/` | `SchemaModel`, `LegacyApplicationModel` | Schema endpoint changes |
| `JsonConverters/` | `FilterQueryModelJsonConverter`, `JsonCreationConverter` | Custom serialization rules for REST bodies |
| `Enumerations/` | `SortType`, `wvOperator`, `wvConnector` | Filter sort/operator/connector enum values in REST |
| Root | `AttributeModel`, `ClassModel`, `DataSetModel`, `FilterBarModel`, `NotificationBarItemModel`, `ValidationProblemModel` | General endpoint response shapes |

**Common mistake:** Developers add a field to `ModelBuilder.cs` or a controller action but forget to add it to the DTO in this project — the field will be silently dropped during serialization.

**⚠️ Breaking change risk:** Any change to DTO shape (renaming properties, removing fields, changing types) is a breaking change for all REST API consumers. Use additive-only changes when possible.

---

#### 1.9.15 FilterQuery Object Model (`Core/FilterQuery/`)

Location: [WorkView/Hyland.WorkView.Core/FilterQuery/](c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView.Core\FilterQuery)
Interface contracts: [WorkView/Hyland.WorkView/FilterQuery/](c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView\FilterQuery)

The `FilterQuery/` directory holds the **typed object model representing a filter execution request** — the intermediate layer between `FilterQueryService` (which builds the request) and `ObjectQueryBuilder` (which generates SQL from it). Understanding this model is essential for debugging filter execution bugs.

```
FilterQueryService.cs
    → builds a FilterQuery object (FilterQuery/)
    → passes to ObjectQueryBuilder.cs
    → ObjectQueryBuilder generates SQL from FilterQuery properties
```

**Core filter query types:**

| File | Purpose |
|---|---|
| `FilterQuery.cs` | Standard filter query — wraps filter definition + runtime constraints |
| `ClassFilterQuery.cs` / `ClassFilterQueryValidator.cs` | Class-scoped filter query; validates before execution |
| `UnityScriptFilterQuery.cs` | Filter query sourced from a Unity Script |
| `WCFFilterQuery.cs` | Filter query over a WCF external source |
| `LobFilterQuery.cs` | Filter query over a LOB/EIS data source |
| `FullTextFilterQuery.cs` | Filter query for full-text search results |
| `SameClassAssociationFilterQueryInformation.cs` | Filter query for same-class associations |
| `AggregateToGenericDataObjectAdapter.cs` | Adapts aggregate results to generic data objects |
| `FilterQuerySummary.cs` | Summary of filter execution results |
| `FilterQueryValidator.cs` | Validates a filter query before SQL generation |

**DynamicQuery/** — Dynamic (ad-hoc, non-saved) filter queries:

| File | Purpose |
|---|---|
| `DynamicQuery.cs` / `DynamicQueryBase.cs` | Runtime-built queries not backed by a saved filter |
| `DynamicQueryClasses.cs` / `CustomQueryClasses.cs` | Class definitions used in dynamic queries |
| `DynamicQueryDataAccess.cs` | Data access for dynamic query results |
| `ODBCDynamicQuery.cs` | Dynamic query over ODBC connection |
| `FilterQueryClasses.cs` | Shared class definitions for filter queries |
| `Table.cs` | Represents a DB table in a query join |

**Props/** — Construction property objects (passed into filter query factories):

| File | Purpose |
|---|---|
| `CreateFilterQueryProps.cs` | Properties for creating a standard filter query |
| `CreateClassFilterQueryProps.cs` | Properties for class-scoped filter queries |
| `CreateSubFilterQueryProps.cs` | Properties for sub-filter queries |
| `CreateFilterDataSetFilterQueryProps.cs` | Properties for DataSet-backed filter queries |
| `CreateDocTypeAssocFilterQueryProps.cs` | Properties for doc-type association queries |
| `FilterQueryConstraintProps.cs` | Runtime constraint override properties |
| `QueryConstructionProps.cs` | Base query construction properties |

**Interface contracts** (`Hyland.WorkView/FilterQuery/`):
- `IFilterQuery.cs`, `IClassFilterQuery.cs`, `IUnityScriptFilterQuery.cs` — filter query contracts
- `IFilterQueryValidator.cs`, `IFilterQueryConstraintProps.cs` — validation and constraint contracts
- `DynamicQuery/` — `IDynamicQueryBase.cs`, `IDynamicClass.cs`, `IDynamicColumn.cs`, `IDynamicWhereClause.cs`, `IDynamicOrderClause.cs`, etc.

**When to look here:** A filter executes but returns wrong data, wrong column count, wrong sort, or uses the wrong data source type — and `ObjectQueryBuilder.cs` alone doesn't explain the issue. The filter query object built in this layer determines what SQL gets generated.

---

#### 1.9.16 Service Interface Contracts (`Hyland.WorkView/Services/`)

Location: [WorkView/Hyland.WorkView/Services/](c:\OnBase\DEV\Core\OnBase.NET\WorkView\Hyland.WorkView\Services)

All service interface contracts live here — **separate from their implementations** in `Hyland.WorkView.Core/Services/`. When navigating the codebase, always locate the interface first to understand the contract, then find the implementation.

| Interface | Implementation |
|---|---|
| `IObjectService.cs` | `Core/Services/ObjectService/ObjectService.cs` |
| `IFilterService.cs` | `Core/Services/FilterService.cs` |
| `IFilterQueryService.cs` | `Core/Services/FilterQueryService.cs` |
| `IAttributeService.cs` | `Core/Services/AttributeService.cs` |
| `ICalendarViewService.cs` | `Core/Services/CalendarViewService.cs` |
| `IPermissionService.cs` | `Core/Services/PermissionService.cs` |
| `IEquationManifestService.cs` | `Core/Services/EquationManifestService.cs` |
| `ISequenceService.cs` | `Core/Services/SequenceService.cs` |
| `INotificationService.cs` | `Core/Services/NotificationService.cs` |
| `IUnityScriptService.cs` | `Core/Services/UnityScriptService.cs` |
| `IOutlookService.cs` | `Core/Services/OutlookService.cs` |
| `ICompositeKeyService.cs` | `Core/Services/CompositeKeyService.cs` |
| `IObjectHistoryService.cs` | `Core/Services/ObjectHistoryService.cs` |
| `IObjectDocumentService.cs` | `Core/Services/ObjectDocumentService.cs` |
| `IFilterBarItemService.cs` | `Core/Services/FilterBarItemService.cs` |
| `IDataSetValueService.cs` | `Core/Services/DataSetValueService.cs` |
| All others (36 total) | Corresponding `Core/Services/XxxService.cs` |

Also in `Hyland.WorkView/Internal/CalculatedAttributes/`:
- `IEquationManifest.cs`, `IEquationProcessor.cs`, `IEquationProcessorFactory.cs`
- `ITransientResolver.cs`, `ITransientInfo.cs`
- `IFunction.cs`, `IObjectFunction.cs`, `ISessionFunction.cs`, `ISimpleFunction.cs`, `IObjectAndClassFunction.cs`
- `INameValueMap.cs`, `INullableValue.cs`

---

## 2. Development Patterns

*This section will be expanded as we document patterns from solving Jira cards.*

### 2.1 Common Implementation Patterns
*(To be populated)*

### 2.2 Service Registration & Dependency Injection
*(To be populated)*

### 2.3 Data Access Layer Patterns
*(To be populated)*

### 2.4 Provider Patterns
*(To be populated)*

### 2.5 REST API Patterns
*(To be populated)*

---

## 3. Testing Guide

*This section will be expanded with testing patterns and best practices.*

### 3.1 Unit Test Conventions
*(To be populated)*

### 3.2 Integration Test Setup
*(To be populated)*

### 3.3 Test Data Management
*(To be populated)*

### 3.4 Common Test Patterns
*(To be populated)*

---

## 4. Debugging & Troubleshooting

*This section will be expanded with debugging techniques discovered while solving issues.*

### 4.1 Common Issues & Solutions
*(To be populated from Jira solutions)*

### 4.2 Layer Parity Issues
*(To be populated)*

### 4.3 Performance Debugging
*(To be populated)*

### 4.4 Security Debugging
*(To be populated)*

---

## 5. Code Review Checklist

*This section will document code review best practices specific to WorkView.*

### 5.1 Correctness Checks
*(To be populated)*

### 5.2 Parity Verification
*(To be populated)*

### 5.3 Test Coverage Requirements
*(To be populated)*

### 5.4 Security Considerations
*(To be populated)*

### 5.5 Performance Considerations
*(To be populated)*

---

## 6. Known Gotchas & Edge Cases

*This section will accumulate tricky scenarios and edge cases discovered during development.*

*(To be populated from real experiences)*

---

## 7. Reference Materials

### Related Documentation
- [WorkView Quick Reference](./WORKVIEW_QUICK_REFERENCE.md) - Quick reference for common patterns and file locations
- [Jira Solutions Archive](./jira-solutions/INDEX.md) - Past issue resolutions

### Key Namespaces
- `Hyland.WorkView` - Interface contracts
- `Hyland.WorkView.Core` - Core implementation
- `Hyland.Unity.WorkView` - Public SDK
- `Hyland.Core.Workview` - Legacy/Core integration layer
- `Hyland.Controls.Web.Workview` - Web controls

### Database Tables
- `rmobject` - Core object records
- `rmclass` - Class definitions
- `rmapplication` - Application configurations
- `rmfilter` - Filter/query definitions
- `rmobjectinstance[classID]` - Class-specific instance data tables
- `rmobjectinstance[classID]_keys` - Composite key tables
- `rmeventlog` - Object history and audit trail

---

## Appendix: Change Log

| Date | Section | Description |
|------|---------|-------------|
| 2026-03-16 | Initial | Created comprehensive guide with architecture deep dive |
| 2026-03-17 | 1.1, 1.3, 1.4, 1.5 | Added OnBase Studio, Web Client, Unity Client (Canvas), and App Server perspectives; added InterfaceServices and Studio file maps; clarified Unity Client vs Unity SDK naming |
| 2026-03-17 | 1.2, 1.4, 1.6, 1.7, 1.8 | Expanded domain model to all ~100 interfaces; added full service layer map (42 services); added external data access variant table; added key large files subsystem guide; completed REST controller list |
| 2026-03-17 | 1.9 | Added Section 1.9: Internal Subsystems & Supporting Infrastructure — per-entity DataAccess layer, Facades layer, CalculatedAttributes deep chain, server-side Doctor framework, Solutions/Components, ReportProcessor, XmlDataMapping/Transfer, InterfaceServices RuleBuilder/QueryResultWriters/Services/Interfaces/Providers, Shared models, internal utilities (Guards/Options/Query/SQLString/SecurityAttributes/Validation), Interface WebAPI variant, ComponentDoctor utility, and complete test project inventory |
| 2026-03-17 | 1.3A, 1.3H, 1.4, 1.9 | Added REST API Models project (1.9.14) with full DTO map; added FilterQuery object model (1.9.15) with all filter query types, DynamicQuery, and Props; added service interface contracts section (1.9.16); extended 1.3A with REST auth/filters/services; extended 1.3H with InterfaceServices Writers (ScreenWriter/ViewWriter) and DataContracts; updated 1.4 Critical Files Map with service interfaces, RuleBuilder, FilterQuery interface contracts |

