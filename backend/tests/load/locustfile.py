"""Locust scenarios for the student high-concurrency paths.

All write scenarios are disabled unless LOAD_TEST_ENV=testing and
LOAD_TEST_ALLOW_WRITES=true are both set.
"""

from __future__ import annotations

import csv
import json
import os
import random
import time
import uuid
from pathlib import Path

import gevent
from locust import FastHttpUser, LoadTestShape, between, events, task


def _flag(name: str) -> bool:
    return os.getenv(name, "").lower() in {"1", "true", "yes"}


def _http_failure(response) -> str:
    error = getattr(response, "error", None)
    if error is not None:
        return f"HTTP {response.status_code}: {error!r}"
    body = (getattr(response, "text", "") or "").strip().replace("\n", " ")
    return f"HTTP {response.status_code}: {body[:200]}" if body else f"HTTP {response.status_code}"


def _load_tokens() -> list[str]:
    path = os.getenv("LOCUST_TOKEN_FILE")
    if not path:
        return []
    with Path(path).open(encoding="utf-8-sig", newline="") as handle:
        rows = csv.DictReader(handle)
        return [row["token"].strip() for row in rows if row.get("token")]


def _load_sessions() -> list[str]:
    path = os.getenv("LOAD_TEST_SESSION_POOL_FILE")
    if not path:
        single = os.getenv("LOAD_TEST_SESSION_ID")
        return [single] if single else []
    with Path(path).open(encoding="utf-8-sig", newline="") as handle:
        rows = csv.DictReader(handle)
        return [row["session_id"].strip() for row in rows if row.get("session_id")]


def _load_deselect_targets() -> list[tuple[str, str]]:
    """Load exact token/session pairs so every deselect request is meaningful."""

    path = os.getenv("LOAD_TEST_DESELECT_TARGET_FILE")
    if not path:
        return []
    with Path(path).open(encoding="utf-8-sig", newline="") as handle:
        rows = csv.DictReader(handle)
        return [
            (row["token"].strip(), row["session_id"].strip())
            for row in rows
            if row.get("token") and row.get("session_id")
        ]


DEFAULT_AI_PROMPTS: tuple[tuple[str, str, float], ...] = (
    ("schedule", "请查询我的实验课表", 20),
    ("schedule", "我下一节实验课是什么？", 20),
    ("progress", "我已经完成了哪些实验？", 12),
    ("progress", "我还有哪些实验没有完成？", 13),
    ("selection", "本学期有哪些实验可以选择？", 8),
    ("selection", "帮我推荐一个不与课程冲突的实验场次", 8),
    ("selection", "为什么我不能选择这个实验？", 4),
    ("guide", "如何进行退课？", 5),
    ("guide", "如何申请调课？", 5),
    ("guide", "如何申请补做实验？", 5),
)


def _load_ai_prompts() -> list[tuple[str, str, float]]:
    """Load a weighted prompt pool, falling back to the business mix above."""

    path = os.getenv("LOAD_TEST_AI_PROMPT_FILE")
    if not path:
        return list(DEFAULT_AI_PROMPTS)

    prompts: list[tuple[str, str, float]] = []
    with Path(path).open(encoding="utf-8-sig", newline="") as handle:
        for line_number, row in enumerate(csv.DictReader(handle), start=2):
            category = (row.get("category") or "custom").strip()
            prompt = (row.get("prompt") or "").strip()
            try:
                weight = float(row.get("weight") or "1")
            except ValueError as exc:
                raise ValueError(
                    f"Invalid AI prompt weight at CSV line {line_number}"
                ) from exc
            if not prompt or weight <= 0:
                raise ValueError(
                    f"AI prompt and positive weight are required at CSV line "
                    f"{line_number}"
                )
            prompts.append((category, prompt, weight))

    if not prompts:
        raise ValueError("LOAD_TEST_AI_PROMPT_FILE contains no prompts")
    return prompts


TOKENS = _load_tokens()
SESSIONS = _load_sessions()
DESELECT_TARGETS = _load_deselect_targets()
AI_PROMPTS = _load_ai_prompts()
SCENARIO = os.getenv("LOCUST_SCENARIO", "student_reads")
ALLOW_WRITES = (
    _flag("LOAD_TEST_ALLOW_WRITES") and os.getenv("LOAD_TEST_ENV") == "testing"
)
ALLOW_AI = _flag("LOAD_TEST_ALLOW_AI") and os.getenv("LOAD_TEST_ENV") == "testing"
AI_PROMPT_MODE = os.getenv("LOAD_TEST_AI_PROMPT_MODE", "random").strip().lower()
TARGET_USERS = int(os.getenv("LOAD_TEST_TARGET_USERS", "4000"))
TOTAL_BURST_REQUESTS = int(os.getenv("LOAD_TEST_TOTAL_REQUESTS", "1000"))
BURST_REQUESTS_PER_USER = 2
FIXED_BURST_USERS = TOTAL_BURST_REQUESTS // BURST_REQUESTS_PER_USER
FLOW_TARGET_USERS = (
    FIXED_BURST_USERS if SCENARIO == "fixed_requests_burst" else TARGET_USERS
)
REQUIRE_UNIQUE_USERS = _flag("LOAD_TEST_REQUIRE_UNIQUE_USERS")
VERIFY_ASYNC_SELECTION = _flag("LOAD_TEST_VERIFY_ASYNC_RESULTS")
ASYNC_SELECTION_TIMEOUT_SECONDS = float(
    os.getenv("LOAD_TEST_ASYNC_SELECTION_TIMEOUT_SECONDS", "120")
)
_token_cursor = 0
_action_cursor = 0
_once_completed = 0
SINGLE_READ_SCENARIOS = {
    "single_dashboard",
    "single_bitmap",
    "single_timetable",
}
SINGLE_ACTION_SCENARIOS = {
    "single_select",
    "single_deselect",
    "single_ai_consult",
}


class StudentBaseUser(FastHttpUser):
    abstract = True
    wait_time = between(1, 3)

    def on_start(self) -> None:
        global _token_cursor
        if not TOKENS:
            self.environment.runner.quit()
            raise RuntimeError("LOCUST_TOKEN_FILE must contain a token column")
        token = TOKENS[_token_cursor % len(TOKENS)]
        _token_cursor += 1
        self._request_headers = {
            "Authorization": f"Bearer {token}",
            "X-Load-Test-Run": os.getenv("LOAD_TEST_RUN_ID", uuid.uuid4().hex),
        }

    def _headers(self) -> dict[str, str]:
        return {
            **self._request_headers,
            "X-Request-ID": uuid.uuid4().hex,
        }

    def _get(self, path: str, name: str) -> None:
        with self.client.get(
            path, name=name, headers=self._headers(), catch_response=True
        ) as response:
            if response.status_code != 200:
                response.failure(_http_failure(response))
                return
            try:
                response.json()
            except json.JSONDecodeError:
                response.failure("invalid JSON")

    def _selection(self, operation: str) -> None:
        if not ALLOW_WRITES or not SESSIONS:
            return
        session_id = random.choice(SESSIONS)
        with self.client.post(
            f"/api/v1/students/me/{operation}-session",
            name=f"POST /students/me/{operation}-session",
            json={"session_id": session_id},
            headers=self._headers(),
            catch_response=True,
        ) as response:
            if response.status_code != 200:
                response.failure(f"HTTP {response.status_code}")
                return
            result = response.json().get("result")
            expected = {
                "processing",
                "ok",
                "full",
                "duplicate",
                "already_selected",
                "not_enrolled",
                "busy",
                "ineligible",
                "conflict",
            }
            if result not in expected:
                response.failure(f"unexpected result={result}")


class StudentReadUser(StudentBaseUser):
    weight = 1 if SCENARIO == "student_reads" else 0

    @task(4)
    def dashboard(self) -> None:
        self._get("/api/v1/students/me/dashboard", "GET /students/me/dashboard")

    @task(2)
    def bitmap(self) -> None:
        self._get("/api/v1/students/me/busy-bitmap", "GET /students/me/busy-bitmap")


class StudentReadOnceUser(StudentBaseUser):
    """One burst: each unique student performs one real page-load flow."""

    weight = (
        1
        if SCENARIO
        in {"student_reads_once", "student_business_burst", "fixed_requests_burst"}
        else 0
    )

    def wait_time(self) -> float:
        """Keep completed users alive without scheduling a duplicate flow."""

        return 60.0

    @task
    def read_once(self) -> None:
        if getattr(self, "_flow_completed", False):
            # Keep the user alive until the last student's flow completes.  If a
            # user exits early, Locust replenishes it to maintain the target and
            # the same token may be executed more than once.
            gevent.sleep(60)
            return

        # StudentPortal loads these two resources concurrently on mount.
        requests = [
            gevent.spawn(
                self._get,
                "/api/v1/students/me/dashboard",
                "GET /students/me/dashboard",
            ),
            gevent.spawn(
                self._get,
                "/api/v1/students/me/busy-bitmap",
                "GET /students/me/busy-bitmap",
            ),
        ]
        gevent.joinall(requests)
        self._flow_completed = True

        global _once_completed
        _once_completed += 1
        if _once_completed >= FLOW_TARGET_USERS and self.environment.runner is not None:
            self.environment.runner.quit()


class SingleReadBurstUser(StudentBaseUser):
    """One selected read operation per student, then stop the whole run."""

    weight = 1 if SCENARIO in SINGLE_READ_SCENARIOS else 0

    def wait_time(self) -> float:
        return 60.0

    def _read_dashboard(self, *, timetable: bool = False) -> None:
        request_name = (
            "GET /students/me/timetable"
            if timetable
            else "GET /students/me/dashboard-summary"
        )
        with self.client.get(
            (
                "/api/v1/students/me/timetable"
                if timetable
                else "/api/v1/students/me/dashboard-summary"
            ),
            name=request_name,
            headers=self._headers(),
            catch_response=True,
        ) as response:
            if response.status_code != 200:
                response.failure(f"HTTP {response.status_code}")
                return
            try:
                payload = response.json()
            except json.JSONDecodeError:
                response.failure("invalid JSON")
                return
            if timetable and not isinstance(payload.get("selected_sessions"), list):
                response.failure("selected_sessions is missing or invalid")

    @task
    def read_once(self) -> None:
        if getattr(self, "_flow_completed", False):
            gevent.sleep(60)
            return

        if SCENARIO == "single_dashboard":
            self._read_dashboard()
        elif SCENARIO == "single_bitmap":
            self._get(
                "/api/v1/students/me/busy-bitmap",
                "GET /students/me/busy-bitmap",
            )
        else:
            self._read_dashboard(timetable=True)
        self._flow_completed = True

        global _once_completed
        _once_completed += 1
        if _once_completed >= TARGET_USERS and self.environment.runner is not None:
            gevent.spawn_later(0.2, self.environment.runner.quit)


class SingleActionBurstUser(StudentBaseUser):
    """Run exactly one selected business operation per student."""

    weight = 1 if SCENARIO in SINGLE_ACTION_SCENARIOS else 0

    def on_start(self) -> None:
        global _action_cursor

        self._action_index = _action_cursor
        _action_cursor += 1
        if SCENARIO == "single_deselect":
            token, self._action_session_id = DESELECT_TARGETS[self._action_index]
            self._request_headers = {
                "Authorization": f"Bearer {token}",
                "X-Load-Test-Run": os.getenv("LOAD_TEST_RUN_ID", uuid.uuid4().hex),
            }
            return

        super().on_start()
        if SCENARIO == "single_select":
            self._action_session_id = SESSIONS[self._action_index % len(SESSIONS)]

    def wait_time(self) -> float:
        return 60.0

    def _write_once(self, operation: str) -> None:
        path = f"/api/v1/students/me/{operation}-session"
        queued_request_id: str | None = None
        with self.client.post(
            path,
            name=f"POST /students/me/{operation}-session",
            json={"session_id": self._action_session_id},
            headers=self._headers(),
            catch_response=True,
            timeout=30,
        ) as response:
            if response.status_code != 200:
                response.failure(_http_failure(response))
                return
            try:
                payload = response.json()
                result = payload.get("result")
            except json.JSONDecodeError:
                response.failure("invalid JSON")
                return

            response.request_meta["name"] = (
                f"POST /students/me/{operation}-session [{result}]"
            )
            expected = (
                {
                    "processing",
                    "ok",
                    "full",
                    "duplicate",
                    "already_selected",
                    "busy",
                    "ineligible",
                    "conflict",
                }
                if operation == "select"
                else {"ok", "not_enrolled", "busy"}
            )
            if result not in expected:
                response.failure(f"unexpected result={result}")
                return
            if operation == "select" and result == "processing":
                request_id = (payload.get("details") or {}).get("request_id")
                if not isinstance(request_id, str) or not request_id:
                    response.failure("processing response missing request_id")
                    return
                queued_request_id = request_id

        if queued_request_id and VERIFY_ASYNC_SELECTION:
            self._wait_for_selection_result(queued_request_id)

    def _wait_for_selection_result(self, request_id: str) -> None:
        deadline = time.monotonic() + ASYNC_SELECTION_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            gevent.sleep(1)
            with self.client.get(
                f"/api/v1/students/me/selection-requests/{request_id}",
                name="GET /students/me/selection-requests/:id",
                headers=self._headers(),
                catch_response=True,
                timeout=10,
            ) as response:
                if response.status_code != 200:
                    response.failure(f"HTTP {response.status_code}")
                    return
                try:
                    result = response.json().get("result")
                except json.JSONDecodeError:
                    response.failure("invalid JSON")
                    return
                if result == "processing":
                    response.success()
                    continue
                response.request_meta["name"] = (
                    f"GET /students/me/selection-requests/:id [{result}]"
                )
                if result not in {"ok", "full", "duplicate", "ineligible", "conflict"}:
                    response.failure(f"unexpected final result={result}")
                return
        raise RuntimeError(f"selection request {request_id} did not finish in time")

    def _ai_once(self) -> None:
        fixed_prompt = os.getenv("LOAD_TEST_AI_PROMPT", "").strip()
        if AI_PROMPT_MODE == "fixed":
            category, prompt = "fixed", fixed_prompt
        else:
            category, prompt, _ = random.choices(
                AI_PROMPTS,
                weights=[item[2] for item in AI_PROMPTS],
                k=1,
            )[0]
        payload = {
            "messages": [
                {
                    "role": "user",
                    "content": prompt,
                }
            ],
            "page_context": {"view": "ai"},
        }
        with self.client.post(
            "/api/v1/students/me/ai-consult",
            name=f"POST /students/me/ai-consult [{category}]",
            json=payload,
            headers=self._headers(),
            catch_response=True,
            timeout=float(os.getenv("LOAD_TEST_AI_TIMEOUT_SECONDS", "120")),
        ) as response:
            if response.status_code == 429:
                response.request_meta["name"] = (
                    f"POST /students/me/ai-consult [{category}][limited-429]"
                )
                response.success()
                return
            if response.status_code != 200:
                response.failure(f"HTTP {response.status_code}")
                return
            try:
                response.json()
            except json.JSONDecodeError:
                response.failure("invalid JSON")

    @task
    def action_once(self) -> None:
        if getattr(self, "_flow_completed", False):
            gevent.sleep(60)
            return

        try:
            if SCENARIO == "single_select":
                self._write_once("select")
            elif SCENARIO == "single_deselect":
                self._write_once("deselect")
            else:
                self._ai_once()
        finally:
            self._flow_completed = True
            global _once_completed
            _once_completed += 1
            if _once_completed >= TARGET_USERS and self.environment.runner is not None:
                gevent.spawn_later(0.2, self.environment.runner.quit)


if SCENARIO in SINGLE_READ_SCENARIOS:

    class SingleReadBurstShape(LoadTestShape):
        """Start the selected number of students as one fixed, one-shot batch."""

        def tick(self):
            return TARGET_USERS, TARGET_USERS


if SCENARIO in SINGLE_ACTION_SCENARIOS:

    class SingleActionBurstShape(LoadTestShape):
        """Use the same one-second, one-shot shape as the read scenarios."""

        def tick(self):
            return TARGET_USERS, TARGET_USERS


if SCENARIO == "student_business_burst":

    class StudentBusinessBurstShape(LoadTestShape):
        """Start all students as a one-second burst, with no ramp-up stages."""

        def tick(self):
            return TARGET_USERS, TARGET_USERS


if SCENARIO == "fixed_requests_burst":

    class FixedRequestsBurstShape(LoadTestShape):
        """Issue an exact, one-shot request batch without continuous users."""

        def tick(self):
            return FIXED_BURST_USERS, FIXED_BURST_USERS


class SelectionUser(StudentBaseUser):
    weight = 1 if SCENARIO in {"selection", "selection_race"} else 0

    @task(3)
    def refresh(self) -> None:
        self._get("/api/v1/students/me/dashboard", "GET /students/me/dashboard")

    @task(2)
    def select(self) -> None:
        self._selection("select")

    @task(1)
    def deselect(self) -> None:
        if SCENARIO != "selection_race":
            self._selection("deselect")


class AIConsultUser(StudentBaseUser):
    weight = 1 if SCENARIO == "ai_consult" else 0

    @task
    def consult(self) -> None:
        payload = {"messages": [{"role": "user", "content": "查询我的课表"}]}
        with self.client.post(
            "/api/v1/students/me/ai-consult",
            name="POST /students/me/ai-consult",
            json=payload,
            headers=self._headers(),
            catch_response=True,
        ) as response:
            if response.status_code not in {200, 429, 503}:
                response.failure(f"HTTP {response.status_code}")


class MixedStudentUser(StudentBaseUser):
    weight = 1 if SCENARIO == "mixed_peak" else 0

    @task(35)
    def dashboard(self) -> None:
        self._get("/api/v1/students/me/dashboard", "GET /students/me/dashboard")

    @task(35)
    def bitmap(self) -> None:
        self._get("/api/v1/students/me/busy-bitmap", "GET /students/me/busy-bitmap")

    @task(20)
    def selection(self) -> None:
        self._selection(random.choice(["select", "deselect"]))

    @task(10)
    def ai(self) -> None:
        payload = {"messages": [{"role": "user", "content": "我有哪些实验课？"}]}
        with self.client.post(
            "/api/v1/students/me/ai-consult",
            name="POST /students/me/ai-consult",
            json=payload,
            headers=self._headers(),
            catch_response=True,
        ) as response:
            if response.status_code not in {200, 429, 503}:
                response.failure(f"HTTP {response.status_code}")


@events.test_start.add_listener
def validate_fixed_request_burst(environment, **kwargs) -> None:
    if SCENARIO != "fixed_requests_burst":
        return
    if TOTAL_BURST_REQUESTS <= 0 or TOTAL_BURST_REQUESTS % 2 != 0:
        raise RuntimeError(
            "LOAD_TEST_TOTAL_REQUESTS must be a positive even number because "
            "each student sends exactly two requests"
        )
    if len(TOKENS) < FIXED_BURST_USERS:
        raise RuntimeError(
            f"fixed_requests_burst requires {FIXED_BURST_USERS} unique tokens; "
            f"only {len(TOKENS)} were loaded"
        )


@events.test_start.add_listener
def validate_single_read_burst(environment, **kwargs) -> None:
    if SCENARIO not in SINGLE_READ_SCENARIOS:
        return
    if TARGET_USERS <= 0:
        raise RuntimeError("LOAD_TEST_TARGET_USERS must be positive")
    if len(TOKENS) < TARGET_USERS:
        raise RuntimeError(
            f"{SCENARIO} requires {TARGET_USERS} unique tokens; "
            f"only {len(TOKENS)} were loaded"
        )


@events.test_start.add_listener
def validate_single_action_burst(environment, **kwargs) -> None:
    if SCENARIO not in SINGLE_ACTION_SCENARIOS:
        return
    if TARGET_USERS <= 0:
        raise RuntimeError("LOAD_TEST_TARGET_USERS must be positive")

    if SCENARIO == "single_deselect":
        if len(DESELECT_TARGETS) < TARGET_USERS:
            raise RuntimeError(
                f"single_deselect requires {TARGET_USERS} token/session pairs in "
                "LOAD_TEST_DESELECT_TARGET_FILE; "
                f"only {len(DESELECT_TARGETS)} were loaded"
            )
    elif len(TOKENS) < TARGET_USERS:
        raise RuntimeError(
            f"{SCENARIO} requires {TARGET_USERS} unique tokens; "
            f"only {len(TOKENS)} were loaded"
        )

    if SCENARIO == "single_select":
        if not ALLOW_WRITES:
            raise RuntimeError(
                "single_select requires LOAD_TEST_ENV=testing and "
                "LOAD_TEST_ALLOW_WRITES=true"
            )
        if not SESSIONS:
            raise RuntimeError(
                "single_select requires LOAD_TEST_SESSION_POOL_FILE or "
                "LOAD_TEST_SESSION_ID"
            )
    elif SCENARIO == "single_deselect" and not ALLOW_WRITES:
        raise RuntimeError(
            "single_deselect requires LOAD_TEST_ENV=testing and "
            "LOAD_TEST_ALLOW_WRITES=true"
        )
    elif SCENARIO == "single_ai_consult" and not ALLOW_AI:
        raise RuntimeError(
            "single_ai_consult requires LOAD_TEST_ENV=testing and "
            "LOAD_TEST_ALLOW_AI=true"
        )
    if SCENARIO == "single_ai_consult":
        if AI_PROMPT_MODE not in {"random", "fixed"}:
            raise RuntimeError("LOAD_TEST_AI_PROMPT_MODE must be random or fixed")
        if (
            AI_PROMPT_MODE == "fixed"
            and not os.getenv("LOAD_TEST_AI_PROMPT", "").strip()
        ):
            raise RuntimeError(
                "LOAD_TEST_AI_PROMPT is required when LOAD_TEST_AI_PROMPT_MODE=fixed"
            )


@events.test_start.add_listener
def validate_environment(environment, **kwargs) -> None:
    available_users = (
        len(DESELECT_TARGETS) if SCENARIO == "single_deselect" else len(TOKENS)
    )
    if REQUIRE_UNIQUE_USERS and available_users < TARGET_USERS:
        raise RuntimeError(
            f"正式压测需要至少 {TARGET_USERS} 个独立学生，当前只有 {available_users} 个"
        )
    if SCENARIO in {"student_reads_once", "student_business_burst"}:
        configured_users = getattr(environment.parsed_options, "num_users", None)
        if len(TOKENS) < TARGET_USERS:
            raise RuntimeError(
                f"{SCENARIO} requires {TARGET_USERS} unique tokens; "
                f"only {len(TOKENS)} were loaded"
            )
        if SCENARIO == "student_reads_once" and configured_users != TARGET_USERS:
            raise RuntimeError(
                f"student_reads_once requires -u {TARGET_USERS}; "
                f"received -u {configured_users}"
            )
    if SCENARIO in {"selection", "selection_race", "mixed_peak"} and not ALLOW_WRITES:
        print(
            "Write tasks disabled: set LOAD_TEST_ENV=testing and "
            "LOAD_TEST_ALLOW_WRITES=true"
        )
