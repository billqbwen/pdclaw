#!/usr/bin/env python3
"""
Claude Session Manager — 维护有状态的 Claude 会话

每个 Issue 有独立的会话，新 Issue 自动创建全新会话上下文。
"""

import json
import logging
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

log = logging.getLogger("claude_session")


class ClaudeSession:
    """管理单个 Issue 的有状态 Claude 会话"""

    def __init__(
        self,
        issue_number: int,
        work_dir: Path,
        model: Optional[str] = None,
        base_url: Optional[str] = None,
    ):
        self.issue_number = issue_number
        self.work_dir = Path(work_dir)
        self.model = model or "deepseek-v4-flash"
        self.base_url = base_url or "https://api.deepseek.com/anthropic"

        # 会话存储路径: .pdca_state/{issue_number}/claude_session.json
        self.session_file = self.work_dir / ".pdca_state" / str(issue_number) / "claude_session.json"
        self.session_file.parent.mkdir(parents=True, exist_ok=True)

        # 会话历史 - 每个步骤的上下文
        self.history: list[dict] = []
        self._load_session()

    def _load_session(self):
        """加载已有会话历史"""
        if self.session_file.exists():
            try:
                data = json.loads(self.session_file.read_text())
                # 验证会话属于当前 issue
                if data.get("issue_number") == self.issue_number:
                    self.history = data.get("history", [])
                    log.info(f"Loaded session for issue #{self.issue_number} with {len(self.history)} turns")
                else:
                    log.warning(f"Session file belongs to different issue, starting fresh")
                    self.history = []
            except Exception as e:
                log.warning(f"Failed to load session: {e}")
                self.history = []

    def _save_session(self):
        """保存会话历史"""
        data = {
            "issue_number": self.issue_number,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "history": self.history,
        }
        self.session_file.write_text(json.dumps(data, indent=2, ensure_ascii=False))

    def _build_context_prompt(self, current_prompt: str, step_name: str, max_history: int = 3) -> str:
        """构建包含历史上下文的 prompt"""
        if not self.history:
            return current_prompt

        # 只取最近的步骤
        recent = self.history[-max_history:]

        context_parts = [
            "=== PDCA Session Context ===",
            f"You are working on GitHub Issue #{self.issue_number}.",
            f"Current step: {step_name.upper()}",
            f"Previous steps completed: {len(self.history)}",
            "",
            "=== Previous Steps Summary ===",
        ]

        for turn in recent:
            prev_step = turn.get('step', 'unknown')
            summary = turn.get('summary', '')
            context_parts.append(f"\n[{prev_step.upper()}]")
            if summary:
                context_parts.append(summary[:500])

        context_parts.extend([
            "",
            "=== Current Task ===",
            current_prompt,
        ])

        return "\n".join(context_parts)

    def execute(
        self,
        prompt: str,
        skill_content: Optional[str] = None,
        step_name: Optional[str] = None,
        timeout: int = 600,
        use_context: bool = True,
    ) -> tuple[bool, str]:
        """
        执行 Claude 调用，自动维护会话上下文

        Args:
            prompt: 当前提示
            skill_content: 技能文件内容
            step_name: 当前步骤名称 (plan/do/check/act)
            timeout: 超时时间
            use_context: 是否使用历史上下文
        """

        # 构建带上下文的 prompt
        if use_context and self.history:
            full_prompt = self._build_context_prompt(prompt, step_name or "unknown")
        else:
            full_prompt = prompt

        # 构建命令
        cmd = [
            "claude",
            "--model", self.model,
            "--permission-mode", "bypassPermissions",
        ]

        if skill_content:
            cmd.extend(["--append-system-prompt", skill_content])

        cmd.extend(["-p", full_prompt])

        # 环境变量
        env = os.environ.copy()
        env["ANTHROPIC_BASE_URL"] = self.base_url
        if os.environ.get("DEEPSEEK_API_KEY"):
            env["ANTHROPIC_API_KEY"] = os.environ["DEEPSEEK_API_KEY"]

        log.info(f"Executing Claude for issue #{self.issue_number}, step {step_name}")
        log.info("[AI] Session execute — model=%s, base_url=%s, has_skill=%s, use_context=%s, history_turns=%d, prompt=%d chars",
                 self.model, self.base_url,
                 "yes" if skill_content else "no",
                 use_context, len(self.history),
                 len(full_prompt))

        try:
            t0 = time.time()
            result = subprocess.run(
                cmd,
                cwd=str(self.work_dir),
                capture_output=True,
                text=True,
                timeout=timeout,
                env=env,
            )
            elapsed = time.time() - t0

            success = result.returncode == 0
            output = result.stdout if success else result.stderr
            log.info("[AI] Session call completed — success=%s, elapsed=%.1fs, rc=%d, output=%d chars",
                     success, elapsed, result.returncode, len(output) if output else 0)

            # 提取摘要（前 500 字符）用于上下文
            summary = output[:500].strip() if success else f"Error: {result.stderr[:200]}"

            # 保存到历史
            self.history.append({
                "step": step_name,
                "prompt_summary": prompt[:200],
                "summary": summary,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "success": success,
            })
            self._save_session()

            return success, output

        except subprocess.TimeoutExpired:
            log.error(f"Session for issue #{self.issue_number} timed out")
            return False, "TIMEOUT"
        except Exception as e:
            log.error(f"Session error: {e}")
            return False, str(e)

    def reset(self):
        """重置当前会话（清除历史）"""
        self.history = []
        if self.session_file.exists():
            self.session_file.unlink()
        log.info(f"Reset session for issue #{self.issue_number}")

    def get_summary(self) -> str:
        """获取会话摘要"""
        steps = [h.get("step") for h in self.history if h.get("success")]
        return f"Issue #{self.issue_number}: {len(self.history)} turns, steps: {steps}"


# 会话管理器（全局缓存）
_sessions: dict[int, ClaudeSession] = {}


def get_session(issue_number: int, work_dir: Path,
                model: Optional[str] = None,
                base_url: Optional[str] = None) -> ClaudeSession:
    """获取或创建 issue 对应的会话

    每个 issue 有独立的会话，新 issue 自动创建全新会话。
    """
    if issue_number not in _sessions:
        _sessions[issue_number] = ClaudeSession(
            issue_number=issue_number,
            work_dir=work_dir,
            model=model,
            base_url=base_url,
        )
    return _sessions[issue_number]


def reset_session(issue_number: int):
    """重置指定 issue 的会话"""
    if issue_number in _sessions:
        _sessions[issue_number].reset()
        del _sessions[issue_number]
    else:
        # 即使没有缓存，也清理文件
        session_file = Path(".pdca_state") / str(issue_number) / "claude_session.json"
        if session_file.exists():
            session_file.unlink()
            log.info(f"Cleaned up session file for issue #{issue_number}")


def clear_all_sessions():
    """清除所有会话缓存（用于程序退出时）"""
    global _sessions
    _sessions.clear()
    log.info("Cleared all session caches")
