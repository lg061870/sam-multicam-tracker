# OjoVial

A scale-model testbed for zero-shot multi-camera vehicle tracking with SAM 3.1 and Muse Spark 1.3, built for the Meta Global AI Developer Hackathon. City cameras at intersections like San José's Circunvalación work as disconnected silos; without access to municipal feeds, this prototype tests cross-camera tracking on a tabletop with two real cameras.

## Goal and current status

A user selects one object in Camera A. The prototype tracks its visible mask and proposes the same object's match in Camera B. Begin with overlapping views, fixed cameras, one distinctive object, and short synchronized recordings. Four cameras, non-overlapping handoffs, and road traffic are later experiments.

Status: working prototype. A web dashboard plays two synchronized clips, tracks a described object in each with SAM 3.1, and asks Muse Spark 1.3 whether the two crops show the same object. That check is a per-moment model verdict with self-reported confidence; it has not been validated as a general re-identification method.

SAM supplies segmentation within each view. Cross-camera identity matching is a separate component and must be tested; SAM alone does not guarantee identity across videos.

## Quick start

Requires Python 3.12, Node.js and a Meta Model API key.

```powershell
git clone https://github.com/lg061870/sam-multicam-tracker.git
cd sam-multicam-tracker
pip install -r requirements.txt
Copy-Item .env.example .env   # then set MODEL_API_KEY in .env, uncommented
cd frontend
npm install
npm run build
cd ..
python server.py
```

Open http://localhost:8000 and use **Add clips** to load two videos. The key is read only from the `MODEL_API_KEY` environment variable or the git-ignored `.env` file. See [Running the Application](#running-the-application) for the workflow.

## First recording

1. Use two phones or cameras simultaneously, fixed at different angles toward the same area.
2. Record 20–30 seconds and clap once for synchronization.
3. Move a distinctive toy car, backpack, or box through both views, pausing briefly.
4. Keep the target visible in both views for the initial test.
5. Record camera names, approximate orientation, and the synchronization clap in project notes.

## Prototype acceptance criteria

- Load two videos and align their timelines using a manually supplied offset.
- Select the target in Camera A and display its mask and ID.
- Propose a match in Camera B with an explicit confidence/uncertainty state.
- Allow confirmation or correction rather than silently forcing a match.
- Export an annotated two-view demonstration.
- Separate tracking failures from identity-matching failures.
- Cache paid model results; do not repeat API calls automatically.

## Existing experiment and its limits

The earlier highway test used the official Meta API cookbook with the text concept "vehicle".
It completed all 880 frames and returned 6,689 masks but only 16 IDs, all introduced within frames 0–3. The rendered video showed incomplete vehicle coverage. This motivates the controlled single-target experiment; it does not establish a general limitation of every SAM configuration.

Preserve the original experiment in Drive:
Systematica Workspace/Projects/Traffic Vision/Results/sam_1793470071

Existing notebook:
https://colab.research.google.com/drive/1yL_i0IfD5rmISOe4yqM5ldw9xoohUBzi

## Work locations

- GitHub: source code, reproducible notebooks, setup, development history.
- Google Drive: original footage, cached API output, renders; keep media out of Git.
- Notion: project management, decisions, progress and submission details.
- Colab: experiments and video processing.
- Muse Code: implementation and testing in a checkout of this repository.

## Windows setup for Muse Code

Install Git first if it is unavailable. In PowerShell, use Meta's official installer:

```powershell
irm https://dev.meta.ai/install.ps1 | iex
git clone https://github.com/lg061870/sam-multicam-tracker.git
cd sam-multicam-tracker
muse
```

On first launch, choose API-key authentication with a key belonging to the credited Meta team/project. Enter credentials locally, never in a chat, source file, notebook output, or Git commit. The repository's private visibility does not make it a suitable secret store.

Muse Code supports pay-as-you-go Model API billing as well as a separate subscription. Meta's hackathon announcement describes free Model API credits for building with Muse Code. Verify the credited team/project in the usage dashboard before substantial work; do not assume a personal browser sign-in uses that same billing context.

Official documentation:
- https://dev.meta.ai/docs/muse-code/auth
- https://dev.meta.ai/docs/overview
- https://developers.meta.com/blog/meta-connect-recap/
- https://dev.meta.ai/events/global-hackathon

## Running the Application

### 1. Web dashboard (recommended)

Build the frontend once, then run the FastAPI server, which serves it:

```powershell
cd frontend
npm install
npm run build
cd ..
python server.py
```

Open **http://localhost:8000**. The server only accepts local connections.

The workflow runs top to bottom in the side panel:

1. **Choose an object**: describe it, pick a suggestion, or use **Suggest objects**. A check mark means it is already tracked and opens for free.
2. **Review tracks**: watch the mask and box overlays on both cameras. If a camera has several instances, choose the same physical object on each side. The strips under the timeline show when it is visible.
3. **Compare cameras**: see the two crops being compared, then run the check. The result is the vision model's own verdict and self-reported confidence. It is evidence for one moment, not proof that re-identification works in general.

Play, scrubbing and opening saved results never make a paid call. Only **Track**, **Suggest objects** and **Compare crops** can, and each result is saved under `cache/`. Keyboard: `Space` plays or pauses, `←` and `→` step one frame.

Clips (`outputs/`) and results (`cache/`) are not in git, so a fresh clone starts empty: use **Add clips**.

For frontend development with hot reloading, keep `python server.py` running and start Vite in a second terminal:

```powershell
cd frontend
npm run dev
```

### 2. Desktop Tkinter viewer (alternative)

```powershell
python -m multicam.viewer --pair outputs/real --out outputs/real/target.json
```

### Tests

```powershell
python -m unittest discover -s tests
cd frontend
npm test
```

## First task for Muse Code

Read this README. Propose the smallest two-camera prototype architecture and implementation plan. Start with offline video loading, synchronization, and target selection. Use cached or synthetic fixtures for development. Identify a suitable cross-camera matching method separately from SAM segmentation. Ask the project owner before making paid inference requests. Do not claim cross-camera identity matching works until demonstrated on the recorded pair.

## Repository handling

Before adding application files, add a .gitignore covering credentials, virtual environments, video inputs, cached model results and rendered outputs. Use environment variables or a local ignored .env file for configuration. Provide an .env.example containing placeholders only.

The project owner leads scope and technical decisions. Preserve evidence and document limitations. No implementation or model use is implied by this scaffold alone.
