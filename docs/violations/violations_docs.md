# Traffic Violations Subsystem Documentation

## 1. Overview & Architecture

The **Sentinel Violations Subsystem** is an integrated traffic violation detection, adjudication, and review pipeline. It is specifically designed to handle real-world surveillance constraints across large-scale municipal CCTV networks.

### Core Architectural Principles

1. **Review Queue, Not Automatic Challans**:
   - The platform strictly separates **recording a violation** from **issuing a penalty/challan**.
   - Automatic fining requires verifiable identity and clear license plates. Across urban CCTV estates, camera heights and angles make plate reading unviable on many cameras.
   - Issuing automatic penalties based on appearance or low-confidence reads risks penalizing innocent citizens at scale. Every candidate violation enters a **Review Queue** (`review_status = 'pending_review'`) for operator adjudication.

2. **Survey Gating (`permitted_violations`)**:
   - Cameras vary widely in resolution, mounting angle, and focal length.
   - Violations are gated by the camera's surveyed capability (`camera_profiles.permitted_violations`).
   - If a camera does not permit a given violation type, the pipeline refuses to record it, preventing false positive floods.

3. **Optical Realism & Glass Penetration**:
   - Certain violations (e.g., `no_helmet`, `triple_riding`) are evaluated on vehicle exteriors.
   - Windscreen-dependent violations (`phone_use`, `no_seatbelt`) require resolving small objects through tinted or reflective windscreens at pole distance.
   - The system explicitly flags violations that need glass penetration (`needs_glass_penetration = true`) so reviewers are aware of optical difficulty before confirming.

---

## 2. Component Structure & File Map

| File Path | Role | Key Components |
|---|---|---|
| `packages/sentinel-pipelines/src/sentinel/pipelines/models/violations.py` | Model Loading, Inference & Box Association | `YoloRiderDetector`, `YoloViolationDetector`, `RiderBox`, `ViolationFinding`, `_riders_from_boxes()`, `SUPPORTED` |
| `packages/sentinel-pipelines/src/sentinel/pipelines/violate.py` | Asynchronous Queue Worker | `ViolateWorker`, composite foreign key management (`read_id`, `rider_slot`), `ReviewStatus` assignment |
| `packages/sentinel-api/src/sentinel/api/routers/violations.py` | FastAPI Endpoints | `GET /violations` (review queue), `GET /violations/types`, `POST /violations/{id}/review`, audit trail |
| `packages/sentinel-api/src/sentinel/api/routers/evidence.py` | Media File Delivery | `GET /media/{kind}/{ref:path}` (serving crops, frames, and evidence) |
| `frontend/src/pages/ViolationsPage.jsx` | Operator Review Interface | Violations review table, `VehicleCutoutThumb`, and collapsible `ViolationModal` |
| `frontend/src/styles.css` | UI Theme & Styling | Glassmorphism table styles, thumbnail transitions, modal overlay & animations |

---

## 3. End-to-End Pipeline Execution Flow

```mermaid
flowchart TD
    A[Vehicle Detection & ByteTrack] --> B[Best Frame Crop Generated: crops/...jpg]
    B --> C[DB Insert: sightings + pipeline_jobs]
    C -->|If camera permits violation types| D[Queue: Pipeline.VIOLATE]
    C -->|If camera permits no violation types| E[sightings.violate_status = 'skipped']
    D --> F[ViolateWorker.process]
    F --> G[Rider Detector: Helmet & Occupant Count]
    G --> H[DB Insert: sighting_riders]
    H --> I[Violation Detector: no_helmet, triple_riding, phone_use]
    I --> J[Filter by review_threshold & Camera Survey]
    J --> K[DB Insert: violations with evidence_ref]
    K --> L[API: GET /violations with crop_ref & evidence_ref]
    L --> M[Frontend: ViolationsPage Table]
    M --> N[Thumbnail Cutout Preview]
    N -->|Click| O[Collapsible ViolationModal]
    O -->|Confirm / Reject| P[API: POST /violations/{id}/review]
```

### Detailed Pipeline Stages

1. **Vehicle Crop Generation (`sentinel-ingest`)**:
   - The ingest pipeline tracks vehicles and extracts the best crop (`crop_ref`), saving it under `var/media/crops/{camera_id}/{date}/{hour}/{read_id}.jpg`.

2. **Rider Extraction (`sighting_riders`)**:
   - For two-wheelers (`motorcycle`, `bicycle`), `YoloRiderDetector` locates riders and checks helmet presence (`None`, `True`, or `False`).
   - Rider rows are inserted into `sighting_riders` first to satisfy the composite foreign key `(read_id, rider_slot)`.

3. **Violation Inference & Thresholding**:
   - Findings below `review_threshold` are dropped.
   - Findings meeting or exceeding `auto_confirm_threshold` (if configured) mark `review_status = 'auto_confirmed'`.
   - All other valid findings are saved as `review_status = 'pending_review'`.
   - If a bounding box is available, evidence crops are written to disk and saved in `evidence_ref`.

4. **API Queue Delivery (`sentinel-api`)**:
   - The endpoint `GET /violations` joins `violations`, `violation_types`, `sightings`, and `camera_profiles`.
   - Sighting vehicle metadata (`crop_ref`, `class`, `colour`, `make`, `model`, `plate_text`) and violation evidence (`evidence_ref`, `evidence_bbox`) are returned in each row.

---

## 4. Frontend Review Interface & Cutout Integration

### Table Cutout Column (`VehicleCutoutThumb`)

In `frontend/src/pages/ViolationsPage.jsx`, each row in the review table presents a visual vehicle cutout preview (`<th>Cutout</th>`):

- **Image Resolution**: Resolves using `api.mediaUrl(violation.crop_ref || violation.evidence_ref)`.
- **Thumbnail Display**:
  - Compact dimensions (`54px` × `40px`) with rounded corners (`6px`) and subtle glass border.
  - Hover zoom effect (`transform: scale(1.08)`) with primary border accent.
  - `loading="lazy"` to preserve browser performance when reviewing large queues.

### Edge Cases Handled

| Edge Case | Behavior |
|---|---|
| **Missing Image Reference** | When `crop_ref` and `evidence_ref` are both null/empty, renders an elegant dashed placeholder (`No crop`) without broken image icons. |
| **Image Load Failure (404/Corrupt)** | The `onError` handler automatically catches network or disk load errors and smoothly transitions to a placeholder (`Failed`). |
| **Inspection from Placeholder** | Clicking the placeholder still opens the detail modal, ensuring operators can inspect metadata and review violations even if images were unlinked. |
| **Safe Attribute Rendering** | Handles missing vehicle classes, empty plates, null confidences, and optional camera trust ratings without runtime errors. |

---

## 5. Collapsible Detail Modal (`ViolationModal`)

Clicking any thumbnail cutout opens an expanded modal dialog providing full visual verification and vehicle context.

### Modal Features

1. **High-Resolution Cutout**:
   - Enlarged container with dark backdrop (`max-height: 380px`, `object-fit: contain`).
   - Displays clear cutout view for visual verification of helmets, occupant counts, or hand-held devices.

2. **Contextual Vehicle & Violation Metadata**:
   - **Vehicle Description**: Full color, make, and model string, falling back to vehicle class.
   - **Plate Read**: Monospace highlighted license plate text or `'None / Not readable'`.
   - **Camera ID**: Source camera identifier.
   - **Timestamp**: Localized detection date and time.
   - **Confidence Score**: Percentage confidence of the model verdict.
   - **Camera Trust Level**: Surveyed optical trust rating of the camera.
   - **Windscreen Check Warning**: Indicates whether the violation required resolving through windscreen glass.
   - **MV Act Section**: Applicable Motor Vehicles Act statutory citation.

3. **Collapsible / Dismissal Mechanisms**:
   - **Backdrop Click**: Clicking outside the modal container dismisses it immediately.
   - **Escape Key Listener**: Pressing the `Escape` key automatically closes the modal (with active listener cleanup on unmount).
   - **Close Buttons**: Dedicated `&times;` top-right button and bottom footer `Close` button.

4. **In-Modal Adjudication**:
   - Reviewers can confirm (`Confirm Violation`) or reject (`Reject Violation`) directly from inside the modal.
   - The action updates the database via `POST /violations/{id}/review`, logs an audit entry, closes the modal, and refreshes the review queue.

---

## 6. Styling & Theme Integration

The styling is defined in `frontend/src/styles.css` adhering to Sentinel's dark glassmorphism system:

- **Colors**: Uses CSS variables `--bg`, `--bg-gradient`, `--panel`, `--line`, `--text`, `--muted`, and `--primary`.
- **Animations**:
  - `modalFadeIn`: Smooth opacity transition on backdrop.
  - `modalScaleUp`: Subtle `scale(0.96) -> scale(1.0)` entry animation on the modal card.
- **Responsiveness**: Grid-based responsive details layout (`repeat(auto-fit, minmax(180px, 1fr))`).

---

## 7. Verification & Testing

- **Unit & Integration Tests**:
  All 201 tests across the project pass:
  ```powershell
  $env:PYTHONPATH="packages/sentinel-core/src;packages/sentinel-registry/src;packages/sentinel-ingest/src;packages/sentinel-pipelines/src;packages/sentinel-correlation/src;packages/sentinel-api/src"
  uv run pytest tests/test_violations.py
  ```
- **Verified Files**:
  - [`frontend/src/pages/ViolationsPage.jsx`](file:///D:/Aayush/Projects/sentinal/project/frontend/src/pages/ViolationsPage.jsx)
  - [`frontend/src/styles.css`](file:///D:/Aayush/Projects/sentinal/project/frontend/src/styles.css)
  - [`docs/violations/violations_docs.md`](file:///D:/Aayush/Projects/sentinal/project/docs/violations/violations_docs.md)
