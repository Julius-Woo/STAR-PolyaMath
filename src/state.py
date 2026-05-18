"""
Problem state management for the Orchestrator system.

Handles:
- Problem lifecycle (plan → steps → solution)
- File I/O (plan.md, PROBLEM_STATE.md, steps/, archive/)
- State persistence and recovery
"""

from __future__ import annotations

import json
import secrets
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from src.utils.session_names import make_session_name


class StepStatus(Enum):
    """Status of a reasoning step."""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    VERIFYING = "verifying"
    CHALLENGING = "challenging"
    VERIFIED = "verified"
    FAILED = "failed"
    ARCHIVED = "archived"


class ProblemStatus(Enum):
    """Overall problem status."""
    INITIALIZED = "initialized"
    PLANNING = "planning"
    PLAN_READY = "plan_ready"
    REASONING = "reasoning"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class StepInfo:
    """Information about a single reasoning step."""
    step_number: int
    title: str
    status: StepStatus = StepStatus.PENDING
    challenge_rounds: int = 0
    trace_back_count: int = 0
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())
    
    def to_dict(self) -> dict:
        return {
            "step_number": self.step_number,
            "title": self.title,
            "status": self.status.value,
            "challenge_rounds": self.challenge_rounds,
            "trace_back_count": self.trace_back_count,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> StepInfo:
        return cls(
            step_number=data["step_number"],
            title=data["title"],
            status=StepStatus(data["status"]),
            challenge_rounds=data.get("challenge_rounds", 0),
            trace_back_count=data.get("trace_back_count", 0),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
        )


@dataclass
class FailureRecord:
    """A structured record of a re-plan or trace-back event.

    Each record carries enough context for the next reasoner attempt to
    avoid repeating the same dead-end (``forbidden_directions``) and to
    keep building on earlier work (``rescued_results``).
    """

    kind: str                              # "replan" | "trace_back"
    plan_version: int                       # plan version at the time of the failure
    step_number: int | None                 # step where trace-back occurred (None for replan)
    triggered_by: str                       # "verifier" | "meta" | "orchestrator"
    summary: str                            # one-line summary of what was abandoned
    reason: str                             # brief reason paragraph (verifier/meta-authored)
    forbidden_directions: list[str] = field(default_factory=list)
    rescued_results: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "plan_version": self.plan_version,
            "step_number": self.step_number,
            "triggered_by": self.triggered_by,
            "summary": self.summary,
            "reason": self.reason,
            "forbidden_directions": list(self.forbidden_directions),
            "rescued_results": list(self.rescued_results),
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> FailureRecord:
        return cls(
            kind=data.get("kind", "replan"),
            plan_version=int(data.get("plan_version", 1)),
            step_number=data.get("step_number"),
            triggered_by=data.get("triggered_by", "orchestrator"),
            summary=data.get("summary", ""),
            reason=data.get("reason", ""),
            forbidden_directions=list(data.get("forbidden_directions", [])),
            rescued_results=list(data.get("rescued_results", [])),
            created_at=data.get("created_at", datetime.now().isoformat()),
        )


class ProblemState:
    """
    Manages the state of a problem-solving session.
    
    Directory structure:
        scratch/<problem_id>/
        ├── state.json            # Serialized machine-readable state
        ├── problem.md            # Original problem
        ├── plan.md               # Step decomposition
        ├── PROBLEM_STATE.md      # Canonical state snapshot for hooks/agents
        ├── solution.md           # Final solution (if completed)
        ├── steps/
        │   ├── step-01-report.md
        │   ├── step-01-verify.md
        │   ├── step-01-debate.md
        │   └── ...
        ├── archive/
        │   └── plan-v1/          # Archived failed plan attempts
        └── code/                 # Python verification scripts
    """
    
    def __init__(self, workspace_root: Path, problem_id: str):
        self.workspace_root = workspace_root
        self.problem_id = problem_id
        self.base_dir = workspace_root / "scratch" / problem_id
        self.state_file = self.base_dir / "state.json"
        
        # State fields
        self.status: ProblemStatus = ProblemStatus.INITIALIZED
        self.current_step: int = 0
        self.total_steps: int = 0
        self.steps: dict[int, StepInfo] = {}
        self.trace_back_count: int = 0
        self.replan_count: int = 0                    # Number of re-plans attempted
        self.session_attempt: int = 0                  # Reasoner-session attempt index for naming
        self.failed_records: list[FailureRecord] = [] # Structured re-plan / trace-back history
        # Pre-plan exploration phase
        self.exploration_attempt: int = 0              # Number of exploration rounds run
        self.exploration_findings: dict[str, Any] = {} # Parsed exploration deliverable (latest round)
        self.current_phase: str = "initializing"      # High-level phase tag for hook routing
        # Per-process salt and within-process session-name cache. Both are
        # regenerated on every ProblemState instantiation and NEVER persisted
        # to state.json: reusing them across runs would collide with stale
        # entries in the Copilot CLI's global session-state cache.
        self.run_salt: str = secrets.token_hex(3)
        self.meta_strategist_session_name: str | None = None
        self.meta_intervention_history: list[dict] = []     # History of Meta-Strategist interventions
        self.reasoner_session_name: str | None = None
        self.created_at: str = datetime.now().isoformat()
        self.updated_at: str = datetime.now().isoformat()
        
        # Statistics tracking
        self.human_intervention_count: int = 0        # Number of human interventions
        self.start_time: float | None = None          # Start timestamp (time.time())
        self.end_time: float | None = None            # End timestamp (time.time())
        
        # Agent call counters
        self.reasoner_calls: int = 0
        self.verifier_calls: int = 0
        self.meta_strategist_calls: int = 0
        
        # Token tracking
        self.total_input_tokens: int = 0
        self.total_output_tokens: int = 0
        self.total_cached_tokens: int = 0
    
    # ========== Directory Management ==========
    
    def ensure_directories(self) -> None:
        """Create all necessary directories."""
        (self.base_dir / "steps").mkdir(parents=True, exist_ok=True)
        (self.base_dir / "archive").mkdir(parents=True, exist_ok=True)
        (self.base_dir / "code").mkdir(parents=True, exist_ok=True)
    
    # ========== File Paths ==========
    
    @property
    def problem_file(self) -> Path:
        return self.base_dir / "problem.md"
    
    @property
    def plan_file(self) -> Path:
        return self.base_dir / "plan.md"
    
    @property
    def problem_state_md_file(self) -> Path:
        return self.base_dir / "PROBLEM_STATE.md"
    
    @property
    def solution_file(self) -> Path:
        return self.base_dir / "solution.md"
    
    @property
    def logs_dir(self) -> Path:
        return self.base_dir / "logs"
    
    @property
    def code_dir(self) -> Path:
        return self.base_dir / "code"
    
    def step_report_file(self, step_num: int) -> Path:
        return self.base_dir / "steps" / f"step-{step_num:02d}-report.md"
    
    def step_verify_file(self, step_num: int) -> Path:
        return self.base_dir / "steps" / f"step-{step_num:02d}-verify.md"
    
    def step_debate_file(self, step_num: int) -> Path:
        return self.base_dir / "steps" / f"step-{step_num:02d}-debate.md"
    
    # ========== Human Intervention Files ==========
    
    @property
    def intervention_count(self) -> int:
        """Count existing intervention files to determine next number."""
        pattern = self.base_dir / "intervention_*.md"
        existing = list(self.base_dir.glob("intervention_*.md"))
        return len(existing)
    
    def intervention_request_file(self, number: int | None = None) -> Path:
        """Get path for human intervention request file (with auto-increment)."""
        if number is None:
            number = self.intervention_count + 1
        return self.base_dir / f"intervention_{number:03d}.md"
    
    def intervention_guidance_file(self, number: int | None = None) -> Path:
        """Get path for human guidance response file (matching request number)."""
        if number is None:
            number = self.intervention_count  # Current intervention
        return self.base_dir / f"guidance_{number:03d}.md"
    
    @property
    def stats_file(self) -> Path:
        return self.base_dir / "solution_stats.md"
    
    # ========== State Persistence ==========
    
    def save(self) -> None:
        """Save state to disk."""
        self.updated_at = datetime.now().isoformat()
        self.ensure_directories()
        
        data = {
            "problem_id": self.problem_id,
            "status": self.status.value,
            "current_step": self.current_step,
            "total_steps": self.total_steps,
            "steps": {str(k): v.to_dict() for k, v in self.steps.items()},
            "trace_back_count": self.trace_back_count,
            "replan_count": self.replan_count,
            "session_attempt": self.session_attempt,
            "failed_records": [r.to_dict() for r in self.failed_records],
            "exploration_attempt": self.exploration_attempt,
            "exploration_findings": self.exploration_findings,
            "current_phase": self.current_phase,
            "meta_intervention_history": self.meta_intervention_history,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            # Statistics
            "human_intervention_count": self.human_intervention_count,
            "start_time": self.start_time,
            "end_time": self.end_time,
            # Agent call counters
            "reasoner_calls": self.reasoner_calls,
            "verifier_calls": self.verifier_calls,
            "meta_strategist_calls": self.meta_strategist_calls,
            # Token tracking
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_cached_tokens": self.total_cached_tokens,
        }
        
        self.state_file.write_text(
            json.dumps(data, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8"
        )
    
    def load(self) -> bool:
        """Load state from disk. Returns True if loaded successfully."""
        if not self.state_file.exists():
            return False
        
        try:
            data = json.loads(self.state_file.read_text(encoding="utf-8"))
            self.status = ProblemStatus(data["status"])
            self.current_step = data["current_step"]
            self.total_steps = data["total_steps"]
            self.steps = {
                int(k): StepInfo.from_dict(v) 
                for k, v in data.get("steps", {}).items()
            }
            self.trace_back_count = data.get("trace_back_count", 0)
            self.replan_count = data.get("replan_count", 0)
            self.session_attempt = data.get("session_attempt", 0)
            self.exploration_attempt = data.get("exploration_attempt", 0)
            self.exploration_findings = data.get("exploration_findings", {}) or {}
            # Prefer new structured records; fall back to legacy
            # `previous_plans: list[str]` and synthesize minimal records.
            raw_records = data.get("failed_records")
            if raw_records:
                self.failed_records = [FailureRecord.from_dict(r) for r in raw_records]
            else:
                legacy = data.get("previous_plans", []) or []
                self.failed_records = [
                    FailureRecord(
                        kind="replan",
                        plan_version=i + 1,
                        step_number=None,
                        triggered_by="orchestrator",
                        summary=f"Plan v{i + 1} (legacy)",
                        reason=(p or "").strip()[:500],
                    )
                    for i, p in enumerate(legacy)
                    if p
                ]
            self.current_phase = data.get("current_phase", "initializing")
            self.meta_intervention_history = data.get("meta_intervention_history", [])
            self.created_at = data.get("created_at", "")
            self.updated_at = data.get("updated_at", "")
            
            # Statistics
            self.human_intervention_count = data.get("human_intervention_count", 0)
            self.start_time = data.get("start_time")
            self.end_time = data.get("end_time")
            # Agent call counters
            self.reasoner_calls = data.get("reasoner_calls", 0)
            self.verifier_calls = data.get("verifier_calls", 0)
            self.meta_strategist_calls = data.get("meta_strategist_calls", 0)
            # Token tracking
            self.total_input_tokens = data.get("total_input_tokens", 0)
            self.total_output_tokens = data.get("total_output_tokens", 0)
            self.total_cached_tokens = data.get("total_cached_tokens", 0)
            return True
        except Exception:
            return False
    
    @classmethod
    def load_or_create(cls, workspace_root: Path, problem_id: str) -> ProblemState:
        """Load existing state or create new one."""
        state = cls(workspace_root, problem_id)
        state.load()  # Will keep defaults if file doesn't exist
        state.ensure_directories()
        return state
    
    # ========== Problem Management ==========
    
    def set_problem(self, problem_text: str) -> None:
        """Save the original problem."""
        self.ensure_directories()
        self.problem_file.write_text(problem_text, encoding="utf-8")
        self.status = ProblemStatus.INITIALIZED
        self.save()
    
    def get_problem(self) -> str | None:
        """Get the original problem text."""
        if self.problem_file.exists():
            return self.problem_file.read_text(encoding="utf-8")
        return None
    
    # ========== Plan Management ==========
    
    def has_plan(self) -> bool:
        """Check if plan exists."""
        return self.plan_file.exists() and self.total_steps > 0
    
    def set_plan(self, plan_text: str, step_titles: list[str]) -> None:
        """Save the plan and initialize steps."""
        self.ensure_directories()
        self.plan_file.write_text(plan_text, encoding="utf-8")
        
        self.total_steps = len(step_titles)
        self.steps = {}
        for i, title in enumerate(step_titles, 1):
            self.steps[i] = StepInfo(step_number=i, title=title)
        
        self.current_step = 1
        self.status = ProblemStatus.PLAN_READY
        self.save()
        self.write_problem_state_md()
    
    def get_plan(self) -> str | None:
        """Get the plan text."""
        if self.plan_file.exists():
            return self.plan_file.read_text(encoding="utf-8")
        return None
    
    def initiate_replan(
        self,
        *,
        triggered_by: str = "orchestrator",
        summary: str = "",
        reason: str = "",
        forbidden_directions: list[str] | None = None,
    ) -> bool:
        """Archive current plan and prepare for re-planning.

        Records a structured ``FailureRecord`` with rescued verified results
        before wiping the step state, so the next reasoner attempt can build
        on prior work without repeating failed directions.

        Returns True if re-plan is allowed, False if max re-plans reached.
        """
        from src.config import CONFIG

        if self.replan_count >= CONFIG.MAX_REPLANS:
            return False

        plan_version = self.replan_count + 1

        # Capture rescue snapshot BEFORE clearing steps.
        rescued = self._collect_rescued_results(
            from_step=1,
            to_step=self.total_steps,
            plan_version=plan_version,
        )

        record = FailureRecord(
            kind="replan",
            plan_version=plan_version,
            step_number=None,
            triggered_by=triggered_by,
            summary=(summary or f"Plan v{plan_version} abandoned").strip(),
            reason=(reason or "(no reason recorded)").strip(),
            forbidden_directions=list(forbidden_directions or []),
            rescued_results=rescued,
        )
        self.failed_records.append(record)

        # Archive current plan and step files to archive/plan-vK/.
        archive_base = self.base_dir / "archive" / f"plan-v{plan_version}"
        archive_base.mkdir(parents=True, exist_ok=True)
        if self.plan_file.exists():
            shutil.move(str(self.plan_file), str(archive_base / "plan.md"))
        steps_dir = self.base_dir / "steps"
        if steps_dir.exists():
            for f in steps_dir.iterdir():
                shutil.move(str(f), str(archive_base / f.name))

        # Reset step state for the next plan attempt.
        self.steps = {}
        self.total_steps = 0
        self.current_step = 0
        self.replan_count += 1
        self.session_attempt += 1
        self.status = ProblemStatus.PLANNING
        self.current_phase = "replanning"
        # Drop reasoner session: a new attempt starts fresh under a new name
        # (derived from session_attempt). See `derived_reasoner_session_name`.
        self.reasoner_session_name = None

        self.save()
        self.write_problem_state_md()
        return True
    
    # ========== Step Management ==========
    
    def get_current_step(self) -> StepInfo | None:
        """Get current step info."""
        return self.steps.get(self.current_step)
    
    def update_step_status(self, step_num: int, status: StepStatus) -> None:
        """Update a step's status."""
        if step_num in self.steps:
            self.steps[step_num].status = status
            self.steps[step_num].updated_at = datetime.now().isoformat()
            self.save()
            self.write_problem_state_md()
    
    def save_step_report(self, step_num: int, report: str) -> Path:
        """Save a step's reasoning report."""
        path = self.step_report_file(step_num)
        path.write_text(report, encoding="utf-8")
        self.update_step_status(step_num, StepStatus.VERIFYING)
        return path
    
    def save_step_verify(self, step_num: int, verification: str) -> Path:
        """Save verification result."""
        path = self.step_verify_file(step_num)
        path.write_text(verification, encoding="utf-8")
        return path
    
    def append_debate(self, step_num: int, role: str, message: str) -> None:
        """Append to debate log."""
        path = self.step_debate_file(step_num)
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        entry = f"\n\n---\n\n## [{timestamp}] {role}\n\n{message}\n"
        
        if path.exists():
            with open(path, "a", encoding="utf-8") as f:
                f.write(entry)
        else:
            header = f"# Step {step_num} Debate Log\n"
            path.write_text(header + entry, encoding="utf-8")
        
        if step_num in self.steps:
            self.steps[step_num].challenge_rounds += 1
            self.save()
    
    def advance_step(self) -> bool:
        """Move to next step. Returns False if no more steps."""
        if self.current_step in self.steps:
            self.steps[self.current_step].status = StepStatus.VERIFIED
        
        if self.current_step >= self.total_steps:
            self.status = ProblemStatus.COMPLETED
            self.current_phase = "completed"
            self.save()
            self.write_problem_state_md()
            return False
        
        self.current_step += 1
        if self.current_step in self.steps:
            self.steps[self.current_step].status = StepStatus.IN_PROGRESS
        self.status = ProblemStatus.REASONING
        self.save()
        self.write_problem_state_md()
        return True
    
    # ========== Archive Management ==========
    
    def archive_step(self, step_num: int) -> Path:
        """Archive a failed step attempt."""
        step_info = self.steps.get(step_num)
        if not step_info:
            raise ValueError(f"Step {step_num} not found")
        
        # Determine version number
        archive_base = self.base_dir / "archive" / f"step-{step_num:02d}"
        version = 1
        while (archive_base.parent / f"step-{step_num:02d}-v{version}").exists():
            version += 1
        
        archive_dir = archive_base.parent / f"step-{step_num:02d}-v{version}"
        archive_dir.mkdir(parents=True, exist_ok=True)
        
        # Move files to archive
        for suffix in ["report", "verify", "debate"]:
            src = self.base_dir / "steps" / f"step-{step_num:02d}-{suffix}.md"
            if src.exists():
                shutil.move(str(src), str(archive_dir / f"{suffix}.md"))
        
        # Update step info
        step_info.status = StepStatus.ARCHIVED
        step_info.trace_back_count += 1
        self.trace_back_count += 1
        self.save()
        
        return archive_dir
    
    def trace_back_to(
        self,
        step_num: int,
        *,
        triggered_by: str = "orchestrator",
        reason: str = "",
        forbidden_directions: list[str] | None = None,
    ) -> None:
        """Trace back to a previous step, archiving current progress.

        Records a ``FailureRecord(kind="trace_back")`` with rescued verified
        results from the abandoned step range, then resets the reasoner
        session name so the next reasoner spawn starts fresh under a new
        attempt-versioned name.
        """
        # Capture rescue snapshot from the steps that are about to be reset.
        rescued = self._collect_rescued_results(
            from_step=step_num,
            to_step=self.current_step,
            plan_version=self.replan_count + 1,
        )

        # Best-effort step title for the summary line.
        target_step = self.steps.get(step_num)
        step_title = target_step.title if target_step else f"Step {step_num}"

        record = FailureRecord(
            kind="trace_back",
            plan_version=self.replan_count + 1,
            step_number=step_num,
            triggered_by=triggered_by,
            summary=f"Trace-back to Step {step_num}: {step_title}",
            reason=(reason or "(no reason recorded)").strip(),
            forbidden_directions=list(forbidden_directions or []),
            rescued_results=rescued,
        )
        self.failed_records.append(record)

        # Archive all steps from step_num onwards.
        for i in range(step_num, self.current_step + 1):
            if i in self.steps and self.steps[i].status != StepStatus.PENDING:
                try:
                    self.archive_step(i)
                except Exception:
                    pass
                self.steps[i].status = StepStatus.PENDING
                self.steps[i].challenge_rounds = 0

        self.current_step = step_num
        self.steps[step_num].status = StepStatus.IN_PROGRESS

        # Reset reasoner session so the new attempt does not carry
        # the discarded transcript forward. New name is versioned via
        # session_attempt (see `derived_reasoner_session_name`).
        self.session_attempt += 1
        self.reasoner_session_name = None

        self.save()
        self.write_problem_state_md()

    # ========== Rescue / Session-name helpers ==========

    def _collect_rescued_results(
        self,
        *,
        from_step: int,
        to_step: int,
        plan_version: int,
    ) -> list[str]:
        """Extract Verified Results blocks from the given step range.

        Used by `initiate_replan` and `trace_back_to` to preserve
        verified lemmas/computations across plan resets, with provenance.
        """
        rescued: list[str] = []
        for i in range(max(1, from_step), max(from_step, to_step) + 1):
            step = self.steps.get(i)
            if not step or step.status != StepStatus.VERIFIED:
                continue
            verify_path = self.step_verify_file(i)
            if not verify_path.exists():
                continue
            try:
                content = verify_path.read_text(encoding="utf-8")
            except Exception:
                continue
            extracted = self._extract_verified_results(content)
            if not extracted:
                continue
            tag = f"(Plan v{plan_version}, Step {i}: {step.title})"
            rescued.append(f"{tag}\n{extracted.strip()}")
        return rescued

    def derived_reasoner_session_name(self) -> str:
        """Current reasoner session name based on session_attempt index.

        attempt=0 → ``reason-<id>-r<salt>``. After re-plan or trace-back,
        attempt is incremented and the name carries an ``-a{N}`` suffix.
        """
        return make_session_name(
            "reason", self.problem_id,
            attempt=self.session_attempt, run_salt=self.run_salt,
        )

    def derived_meta_session_name(self) -> str:
        """Current meta-strategist session name (no versioning)."""
        return make_session_name("meta", self.problem_id, run_salt=self.run_salt)

    def derived_verifier_session_name(self, step_num: int) -> str:
        """Verifier session name for a given step (no versioning)."""
        return make_session_name(
            "verify", self.problem_id, step=step_num, run_salt=self.run_salt,
        )

    def derived_explore_session_name(self) -> str:
        """Explore session name; one fresh session per exploration round."""
        return make_session_name(
            "explore", self.problem_id,
            attempt=self.exploration_attempt, run_salt=self.run_salt,
        )

    @property
    def exploration_file(self) -> Path:
        """Current-round exploration deliverable (`scratch/<id>/exploration.md`)."""
        return self.base_dir / "exploration.md"

    def archive_exploration(self) -> Path | None:
        """Move current-round exploration.md to `archive/exploration-vK/`. No-op if absent."""
        if not self.exploration_file.exists():
            return None
        archive_base = self.base_dir / "archive" / f"exploration-v{self.exploration_attempt}"
        archive_base.mkdir(parents=True, exist_ok=True)
        target = archive_base / "exploration.md"
        shutil.move(str(self.exploration_file), str(target))
        return target

    # ========== Phase Management ==========

    def set_phase(self, phase: str) -> None:
        """Set the high-level phase tag and persist + re-render snapshot."""
        self.current_phase = phase
        self.save()
        self.write_problem_state_md()

    # ========== Canonical State Snapshot (PROBLEM_STATE.md) ==========

    _STATUS_ICONS = {
        StepStatus.PENDING: "⬜",
        StepStatus.IN_PROGRESS: "🔄",
        StepStatus.VERIFYING: "🔍",
        StepStatus.CHALLENGING: "⚔️",
        StepStatus.VERIFIED: "✅",
        StepStatus.FAILED: "❌",
        StepStatus.ARCHIVED: "📦",
    }

    def write_problem_state_md(self) -> None:
        """Render the canonical state snapshot for hooks/agents to read."""
        self.ensure_directories()

        plan_version = self.replan_count + 1
        now = datetime.now().isoformat(timespec="seconds")

        lines: list[str] = []
        lines.append(f"# Problem State: {self.problem_id}")
        lines.append("")
        lines.append("> Canonical state snapshot. Written by orchestrator on every state change.")
        lines.append("> Hooks read this and inject relevant sections into agent sessions.")
        lines.append("")

        # Problem Statement (full text — single source of truth for the agent)
        lines.append("## Problem Statement")
        lines.append("")
        problem_text = self.get_problem()
        if problem_text:
            lines.append(problem_text.strip())
        else:
            lines.append("(problem.md not yet written)")
        lines.append("")

        # Status
        lines.append("## Status")
        lines.append("")
        lines.append(f"- Phase: {self.current_phase}")
        lines.append(f"- Steps: {self.current_step}/{self.total_steps}")
        lines.append(f"- Trace-backs: {self.trace_back_count}")
        lines.append(f"- Re-plans: {self.replan_count}")
        lines.append(f"- Updated: {now}")
        lines.append("")

        # Current Step
        lines.append("## Current Step")
        lines.append("")
        if self.total_steps == 0:
            # SOLVED-via-exploration path skips plan generation.
            # Surface a meaningful goal so the verifier's Goal Gate is not
            # comparing against "(no plan yet)".
            ef = self.exploration_findings or {}
            assess = (ef.get("assessment") or "").upper()
            if self.current_phase in ("writing_solution", "verifying_solution") and assess == "SOLVED":
                lines.append(
                    "**Whole-solution review** (SOLVED via exploration round "
                    f"{self.exploration_attempt}). Goal: confirm the solution.md "
                    "answers the original problem with a complete, correct argument."
                )
            elif self.current_phase == "exploration":
                lines.append(
                    f"**Exploration round {max(1, self.exploration_attempt)}** — "
                    "probe structure; produce exploration.md per the "
                    "`exploration-protocol` skill."
                )
            else:
                lines.append("(no plan yet)")
        else:
            cur = self.steps.get(self.current_step)
            if cur:
                lines.append(
                    f"**Step {cur.step_number}: {cur.title}** — status: {cur.status.value}"
                )
            else:
                lines.append(f"(step {self.current_step} not defined)")
        lines.append("")

        # Plan
        lines.append(f"## Plan (v{plan_version})")
        lines.append("")
        if self.total_steps == 0:
            lines.append("(plan not generated yet)")
        else:
            for i in range(1, self.total_steps + 1):
                step = self.steps.get(i)
                if step:
                    icon = self._STATUS_ICONS.get(step.status, "❓")
                    suffix = ""
                    if i == self.current_step and step.status not in (
                        StepStatus.VERIFIED, StepStatus.ARCHIVED
                    ):
                        suffix = "  ← current"
                    lines.append(f"{i}. {icon} Step {i}: {step.title}{suffix}")
                else:
                    lines.append(f"{i}. ⬜ Step {i}: (not defined)")
        lines.append("")

        # Verified Results
        lines.append("## Verified Results")
        lines.append("")
        # Section A: results from the current plan attempt.
        verified_blocks: list[str] = []
        for i in range(1, self.total_steps + 1):
            step = self.steps.get(i)
            if not step or step.status != StepStatus.VERIFIED:
                continue
            verify_path = self.step_verify_file(i)
            if not verify_path.exists():
                continue
            try:
                content = verify_path.read_text(encoding="utf-8")
            except Exception:
                continue
            extracted = self._extract_verified_results(content)
            if extracted:
                verified_blocks.append(f"#### Step {i}: {step.title}\n\n{extracted}")
        lines.append(f"### From current plan (v{plan_version})")
        lines.append("")
        if verified_blocks:
            lines.append("\n\n".join(verified_blocks))
        else:
            lines.append("(none yet)")
        lines.append("")

        # Section B: results rescued from earlier abandoned attempts.
        rescued_blocks: list[str] = []
        for rec in self.failed_records:
            for item in rec.rescued_results:
                rescued_blocks.append(item.strip())
        if rescued_blocks:
            lines.append("### Rescued from earlier attempts")
            lines.append("")
            lines.append("\n\n".join(rescued_blocks))
            lines.append("")

        # Exploration Findings
        ef = self.exploration_findings or {}
        if ef:
            lines.append(
                f"## Exploration Findings (round {ef.get('round') or self.exploration_attempt})"
            )
            lines.append("")
            assessment = ef.get("assessment") or "(unspecified)"
            lines.append(f"**Assessment**: {assessment}")
            lines.append("")
            for label, key in (
                ("Worked (verified observations)", "worked"),
                ("Tried (approaches)", "tried"),
                ("Failed (with counter-evidence)", "failed"),
                ("Candidate approaches", "candidates"),
            ):
                items = ef.get(key) or []
                if items:
                    lines.append(f"### {label}")
                    for it in items:
                        lines.append(f"- {it}")
                    lines.append("")
            sketch = ef.get("solution_sketch")
            if sketch:
                lines.append("### Solution sketch (SOLVED path)")
                lines.append("")
                lines.append(sketch.strip())
                lines.append("")

        # Confirmed Failures
        lines.append("## Confirmed Failures")
        lines.append("")
        if not self.failed_records:
            lines.append("(none — first attempt)")
        else:
            for rec in self.failed_records:
                if rec.kind == "replan":
                    header = (
                        f"### Plan v{rec.plan_version} — re-plan "
                        f"(triggered by {rec.triggered_by})"
                    )
                else:  # trace_back
                    header = (
                        f"### Plan v{rec.plan_version} → Step {rec.step_number} "
                        f"trace-back (triggered by {rec.triggered_by})"
                    )
                lines.append(header)
                lines.append("")
                if rec.summary:
                    lines.append(f"**Summary**: {rec.summary}")
                if rec.reason:
                    lines.append(f"**Reason**: {rec.reason}")
                if rec.forbidden_directions:
                    lines.append("**Forbidden directions**:")
                    for d in rec.forbidden_directions:
                        lines.append(f"- {d}")
                lines.append("")
        lines.append("")

        # Folder Layout (concise)
        lines.append("## Folder Layout")
        lines.append("")
        lines.append("```")
        lines.append(f"scratch/{self.problem_id}/")
        lines.append("├── problem.md")
        lines.append("├── plan.md")
        lines.append("├── PROBLEM_STATE.md   # this file")
        lines.append("├── state.json")
        lines.append("├── steps/             # step-NN-report.md, step-NN-verify.md")
        lines.append("├── code/              # step{NN}_*.py")
        lines.append("├── archive/           # plan-vK/")
        lines.append("└── solution.md        # final solution (when verified)")
        lines.append("```")
        lines.append("")

        self.problem_state_md_file.write_text("\n".join(lines), encoding="utf-8")
    
    # ========== Solution ==========
    
    def save_solution(self, solution: str) -> Path:
        """Save the final solution."""
        header = f"""# Solution: {self.problem_id}

**Completed:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
**Total Steps:** {self.total_steps}
**Trace Backs:** {self.trace_back_count}

---

"""
        self.solution_file.write_text(header + solution, encoding="utf-8")
        self.status = ProblemStatus.COMPLETED
        self.current_phase = "completed"
        self.save()
        self.write_problem_state_md()
        return self.solution_file
    
    # ========== Context Building ==========
    
    def get_completed_steps_context(self) -> str:
        """Get summary of all completed steps for context.
        
        Uses Verified Results from Verifier if available, falls back to full report.
        
        Note: Uses total_steps instead of current_step to ensure all verified steps
        are included, especially when generating the final solution.
        """
        context_parts = []
        
        for i in range(1, self.total_steps + 1):
            step = self.steps.get(i)
            if step and step.status == StepStatus.VERIFIED:
                # Try to get verified results summary first
                verify_file = self.step_verify_file(i)
                verified_results = None
                if verify_file.exists():
                    verify_content = verify_file.read_text(encoding="utf-8")
                    verified_results = self._extract_verified_results(verify_content)
                
                if verified_results:
                    # Use concise summary from Verifier
                    context_parts.append(f"## Step {i}: {step.title}\n\n{verified_results}")
                else:
                    # Fall back to full report
                    report_file = self.step_report_file(i)
                    if report_file.exists():
                        report = report_file.read_text(encoding="utf-8")
                        context_parts.append(f"## Step {i}: {step.title}\n\n{report}")
        
        if context_parts:
            return "# Completed Steps\n\n" + "\n\n---\n\n".join(context_parts)
        return ""

    def get_completed_steps_summary(self) -> str:
        """Return a shorter summary of completed steps for prompts."""
        return self.get_completed_steps_context()
    
    def _extract_verified_results(self, verify_content: str) -> str | None:
        """Extract Verified Results section from Verifier's response."""
        import re
        
        # Look for Verified Results section
        patterns = [
            r"## Verified Results\s*\n(.*?)(?=\n##|\n\*\*|\Z)",
            r"### Verified Results\s*\n(.*?)(?=\n###|\n##|\Z)",
            r"Verified Results:\s*\n(.*?)(?=\n\n\n|\Z)",
        ]
        
        for pattern in patterns:
            match = re.search(pattern, verify_content, re.DOTALL | re.IGNORECASE)
            if match:
                result = match.group(1).strip()
                if result and len(result) > 10:  # Sanity check
                    return result
        
        return None
    
    # ========== Statistics ==========
    
    def record_agent_call(self, agent_type: str, usage: dict | None = None) -> None:
        """
        Record an agent call and accumulate token usage.
        
        Args:
            agent_type: One of 'reasoner', 'verifier', 'orchestrator', 'meta_strategist'
            usage: Optional dict with 'input_tokens', 'output_tokens', 'cached_tokens'
        """
        # Increment agent call counter
        if agent_type == "reasoner":
            self.reasoner_calls += 1
        elif agent_type == "verifier":
            self.verifier_calls += 1
        elif agent_type == "meta_strategist":
            self.meta_strategist_calls += 1
        
        # Accumulate token usage
        if usage:
            self.total_input_tokens += usage.get("input_tokens", 0)
            self.total_output_tokens += usage.get("output_tokens", 0)
            self.total_cached_tokens += usage.get("cached_tokens", 0)
        
        # Auto-save to persist counters
        self.save()
    
    def get_code_execution_count(self) -> int:
        """Count Python files created in code folder."""
        code_dir = self.base_dir / "code"
        if not code_dir.exists():
            return 0
        return len(list(code_dir.glob("*.py")))
    
    def get_first_attempt_pass_rate(self) -> tuple[int, int]:
        """
        Calculate first-attempt pass rate.
        Returns (passed_first_attempt, total_verified_steps).
        """
        passed = 0
        total = 0
        for step in self.steps.values():
            if step.status == StepStatus.VERIFIED:
                total += 1
                if step.challenge_rounds == 0:
                    passed += 1
        return passed, total
    
    def get_avg_verification_rounds(self) -> float:
        """Calculate average verification rounds per verified step."""
        verified_steps = [s for s in self.steps.values() if s.status == StepStatus.VERIFIED]
        if not verified_steps:
            return 0.0
        total_rounds = sum(s.challenge_rounds for s in verified_steps)
        return total_rounds / len(verified_steps)
    
    def get_total_agent_calls(self) -> int:
        """Get total number of agent calls."""
        return (self.reasoner_calls + self.verifier_calls + 
                self.meta_strategist_calls)
    
    def format_tokens(self, tokens: int) -> str:
        """Format token count with k/M suffix."""
        if tokens >= 1_000_000:
            return f"{tokens / 1_000_000:.1f}M"
        elif tokens >= 1_000:
            return f"{tokens / 1_000:.1f}k"
        return str(tokens)
    
    def get_duration_seconds(self) -> float | None:
        """Get the total solving duration in seconds."""
        if self.start_time and self.end_time:
            return self.end_time - self.start_time
        return None
    
    def format_duration(self, seconds: float) -> str:
        """Format duration in human-readable format."""
        if seconds < 60:
            return f"{seconds:.1f} seconds"
        elif seconds < 3600:
            minutes = int(seconds // 60)
            secs = seconds % 60
            return f"{minutes}m {secs:.0f}s"
        else:
            hours = int(seconds // 3600)
            minutes = int((seconds % 3600) // 60)
            return f"{hours}h {minutes}m"
    
    def write_stats(self) -> Path:
        """
        Write solution statistics to solution_stats.md.
        Returns the path to the stats file.
        """
        from datetime import datetime
        
        # Calculate duration
        duration = self.get_duration_seconds()
        duration_str = self.format_duration(duration) if duration else "N/A"
        
        # Get intervention count
        intervention_count = self.human_intervention_count
        
        # Gather other stats
        total_steps = self.total_steps
        verified_steps = sum(1 for s in self.steps.values() if s.status == StepStatus.VERIFIED)
        replan_count = self.replan_count
        traceback_count = self.trace_back_count
        
        # New metrics
        code_count = self.get_code_execution_count()
        first_pass, total_verified = self.get_first_attempt_pass_rate()
        if total_verified:
            first_pass_rate = f"{first_pass}/{total_verified} ({100*first_pass/total_verified:.0f}%)"
        elif total_steps == 0 and self.exploration_findings.get("assessment") == "SOLVED":
            first_pass_rate = "n/a (solved via exploration, no plan)"
        else:
            first_pass_rate = "N/A"
        avg_verify_rounds = self.get_avg_verification_rounds()
        total_agent_calls = self.get_total_agent_calls()
        # Copilot CLI 1.0.39 emits per-message output tokens but no input /
        # cache tokens, so only the output total is surfaced here.
        output_tokens_str = self.format_tokens(self.total_output_tokens) if self.total_output_tokens else "n/a"
        
        # Format timestamps
        start_str = datetime.fromtimestamp(self.start_time).strftime("%Y-%m-%d %H:%M:%S") if self.start_time else "N/A"
        end_str = datetime.fromtimestamp(self.end_time).strftime("%Y-%m-%d %H:%M:%S") if self.end_time else "N/A"
        
        # Surface exploration outcome
        ef = self.exploration_findings or {}
        explore_attempt = self.exploration_attempt
        if explore_attempt > 0:
            explore_line = (
                f"| **Exploration** | round{'s' if explore_attempt > 1 else ''} "
                f"{explore_attempt}, assessment={ef.get('assessment', 'unknown')} |"
            )
        else:
            explore_line = "| **Exploration** | not run |"

        # Build the stats markdown
        content = f"""# Solution Statistics

## Problem: {self.problem_id}

### Summary
| Metric | Value |
|--------|-------|
| **Total Duration** | {duration_str} |
| **Total Output Tokens** | {output_tokens_str} |
| **Total Agent Calls** | {total_agent_calls} |
| **Human Interventions** | {intervention_count} |
{explore_line}

### Step Statistics
| Metric | Value |
|--------|-------|
| Steps Planned | {total_steps} |
| Steps Verified | {verified_steps} |
| First-attempt Pass Rate | {first_pass_rate} |
| Avg Verification Rounds | {avg_verify_rounds:.1f} |
| Re-plan Count | {replan_count} |
| Trace-back Count | {traceback_count} |

### Agent Calls
| Agent | Calls |
|-------|-------|
| Reasoner | {self.reasoner_calls} |
| Verifier | {self.verifier_calls} |
| Meta-Strategist | {self.meta_strategist_calls} |

### Code Execution
| Metric | Value |
|--------|-------|
| Python Scripts Created | {code_count} |

### Timestamps
| Event | Time |
|-------|------|
| Start | {start_str} |
| End | {end_str} |

---
*Generated automatically by Orchestrator*
"""
        
        self.stats_file.write_text(content, encoding="utf-8")
        return self.stats_file
