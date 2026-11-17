# 2.5.2 — Release retry

Rebuild the new UI and CPU fixes after configuring the cross-repository release token.

# 2.5.1 — Windows build fix

Read the source as UTF-8 during release validation so Unicode UI text does not break the Windows build. Includes the redesigned UI and disabled previews from 2.5.0.

# 2.5.0 — New UI with reduced background work

Based on ui-redesign / v2.3.4. Preserves the new UI; corrects its embedded 1.3.1 version to 2.5.0. The build rejects mismatched version tags and publishes successful artifacts as latest. Update dialogs and installation recheck version order.

Automatic preview capture and thumbnail generation are disabled. Live-view requests explain that previews are disabled. OCR and OpenCV use one processing thread, scanning waits at least six seconds per account, and yields between checks. UI polling is 2.5 seconds and history updates only on its page. Earlier secret-popup recognition and fresh-frame confirmation protections are retained. Discord screenshots are still captured for detections.

Tests cover detection, confirmation, popup regions, older-version comparisons, and absence of preview capture. Actual Windows CPU usage and the redesigned UI require on-device validation. Detection may be slower or miss short-lived popups. CPU use cannot be guaranteed near zero while OCR is active.
