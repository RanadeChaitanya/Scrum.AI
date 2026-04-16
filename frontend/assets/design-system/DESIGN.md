```markdown
# Design System Strategy: The Fluid Workflow

## 1. Overview & Creative North Star
The Creative North Star for this design system is **"The Intelligent Pulse."** 

Unlike traditional project management tools that feel like static spreadsheets, this system is designed to feel alive, breathing, and predictive. We are moving away from "Industrial Scrum"—rigid grids, heavy borders, and cluttered dashboards—toward an **Editorial Intelligence** aesthetic. This approach prioritizes cognitive ease through high-contrast typography scales, intentional asymmetry, and a "layered glass" depth model. By utilizing soft teal and botanical greens against a pristine neutral canvas, we create an environment that feels technologically advanced yet human-centric.

---

## 2. Colors: Tonal Depth & The "No-Line" Rule

This system rejects the "box-in-box" layout of the early 2000s. We define space through weight and light, not lines.

### The "No-Line" Rule
**Explicit Instruction:** Do not use 1px solid borders to section off content. 
Structure is achieved through background color shifts. For example, a task detail pane using `surface_container_low` should sit directly against a `background` workspace. The boundary is defined by the shift in value, creating a sophisticated, "borderless" interface.

### Surface Hierarchy & Nesting
Treat the UI as a series of physical layers—like stacked sheets of fine paper or frosted glass:
- **Level 0 (Base):** `background` (#f7f9fb) – The canvas.
- **Level 1 (Sections):** `surface_container_low` (#f2f4f6) – For sidebar backgrounds or secondary content areas.
- **Level 2 (Cards):** `surface_container_lowest` (#ffffff) – Used for primary Kanban cards or interactive modules to provide "pop."
- **Level 3 (Pop-overs):** `surface_bright` (#f7f9fb) – For floating AI suggestions or tooltips.

### The "Glass & Gradient" Rule
To evoke a "futuristic" feel, primary CTAs and AI-driven insights should use **Signature Textures**. 
- **The Pulse Gradient:** Transition from `primary` (#006b5f) to `primary_container` (#14b8a6) at a 135-degree angle. This adds a "soul" to the automation that flat colors cannot provide.
- **Glassmorphism:** For floating navigation or AI status bars, use `surface_container_lowest` at 70% opacity with a `20px` backdrop-blur.

---

## 3. Typography: Editorial Authority

We use a dual-typeface system to balance technical precision with approachable modernism.

*   **Display & Headlines (Space Grotesk):** This is our "Futuristic" anchor. Use `display-lg` and `headline-md` for high-level data points (e.g., Sprint Velocity) and page titles. The geometric nature of Space Grotesk communicates precision.
*   **Interface & Body (Inter):** Inter is used for all functional text. Its high x-height ensures readability in complex Kanban boards.
*   **Hierarchy as Navigation:** Use `label-md` in all-caps with `0.05em` letter spacing for metadata (e.g., STORY POINTS) to create an authoritative, editorial feel.

---

## 4. Elevation & Depth: Tonal Layering

We avoid the "dirty" look of heavy drop shadows. Depth is an expression of light.

*   **The Layering Principle:** Instead of shadows, stack surfaces. A `surface_container_lowest` card on top of a `surface_container_low` background creates a natural, soft lift.
*   **Ambient Shadows:** If a card must float (e.g., a dragged Kanban task), use a shadow tinted with `on_surface` (#191c1e) at **4% opacity** with a `32px` blur and `8px` Y-offset. It should feel like a soft glow of light, not a dark stain.
*   **The "Ghost Border" Fallback:** For accessibility in high-density views, use `outline_variant` at **15% opacity**. This provides a "suggestion" of a boundary without breaking the minimal aesthetic.

---

## 5. Components

### 5.1 Buttons (The Kinetic Trigger)
*   **Primary:** Uses "The Pulse Gradient." On hover, the `primary_container` glow expands slightly.
*   **Tertiary/Ghost:** No container. Use `primary` text. On hover, apply a `surface_container_high` background with a `0.5rem` (sm) corner radius.
*   **Animation:** Use a `200ms cubic-bezier(0.4, 0, 0.2, 1)` for all state transitions.

### 5.2 Kanban Boards (The Fluid Grid)
*   **Columns:** No vertical lines. Use `surface_container_low` for the column track.
*   **Cards:** Use `surface_container_lowest` with `md` (0.75rem) rounded corners.
*   **AI Indicators:** Use a `tertiary_container` (#27aef3) "Glow Pulse" (a subtle 2px outer shadow) to highlight tasks the AI has automatically updated.

### 5.3 Input Fields (The Focus State)
*   **Resting:** `surface_container_high` background, no border.
*   **Focus:** Transition background to `surface_container_lowest` and apply a 2px "Ghost Border" using `primary`.
*   **Micro-interaction:** The label should shift from `body-md` to `label-sm` and change color to `primary` upon focus.

### 5.4 Status Indicators (The Semantic Signal)
Avoid the "traffic light" cliché. Use subtle tonal chips:
*   **Done:** `secondary_container` background with `on_secondary_container` text.
*   **Blocked:** `error_container` background with `on_error_container` text.
*   **In Progress:** `tertiary_container` background with `on_tertiary_container` text.

---

## 6. Do’s and Don’ts

### Do:
*   **Embrace White Space:** Use the `xl` (1.5rem) spacing unit between major sections to let the AI-generated data "breathe."
*   **Use Intentional Asymmetry:** Align primary actions to the right, but keep "Insights" or "AI Summaries" in slightly offset, wider-margined containers to draw the eye.
*   **Nesting:** Always place a lighter surface on a darker surface to create "lift."

### Don’t:
*   **No 100% Black:** Never use #000000. Use `on_surface` (#191c1e) for text to maintain the soft, premium feel.
*   **No Hard Edges:** Avoid `none` or `sm` roundedness for large containers. Stick to `lg` (1rem) for main cards to maintain the "Soft Minimalism" theme.
*   **No Dividers:** If you feel the need to add a horizontal rule (`<hr>`), instead try adding `24px` of vertical space or a slight background color shift. 

---

## 7. Signature AI Component: The "Intelligence Rail"
For this platform, we introduce the **Intelligence Rail**—a vertical element on the right side of the screen using `surface_container_highest`. It houses the AI’s real-time suggestions. It should use a `backdrop-blur` and appear to sit "behind" the main Kanban board, creating a sense of depth that suggests the AI is the foundation upon which the work rests.```