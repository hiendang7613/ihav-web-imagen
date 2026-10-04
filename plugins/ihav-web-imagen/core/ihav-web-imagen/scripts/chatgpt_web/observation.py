"""Validated rendered observations and the current turn's durable snapshot.

This is snapshot reconciliation, not a response stream assembler. Missing answer
prefixes stay unresolved; previously observed text is never spliced into a tail.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Literal

from pydantic import Field, ValidationError, field_validator, model_validator

from .core import ResearchError, StrictModel, digest, json_bytes, saved_url


class TurnBinding(StrictModel):
    conversation_url: str
    user_turn_id: str | None = None
    assistant_turn_id: str | None = None

    @field_validator('conversation_url')
    @classmethod
    def permanent_url(cls, value):
        saved_url(value)
        return value

    @field_validator('user_turn_id', 'assistant_turn_id')
    @classmethod
    def nonblank_id(cls, value):
        if value is not None and (not value.strip() or len(value) > 512):
            raise ValueError('Invalid logical turn ID')
        return value


class ResponseLink(StrictModel):
    url: str
    title: str = ''


class Observation(StrictModel):
    url: str | None = None
    marker_seen: bool = False
    generating: bool = False
    turn_ended: bool = False
    text: str = ''
    markdown: str = ''
    links: list[ResponseLink] = Field(default_factory=list)
    web_search_evidence: list[dict] = Field(default_factory=list)
    ownership: Literal['marker', 'bound_ids', 'unverified'] = 'unverified'
    binding: TurnBinding | None = None
    response_present: bool = False
    projection_state: Literal['awaiting', 'empty', 'snapshot', 'unresolved'] = 'empty'
    projection_reason: str | None = None
    terminal_reason: Literal['response_error', 'stopped_thinking'] | None = None
    blocks: list[str] = Field(default_factory=list)
    unresolved_citations: int = Field(default=0, ge=0)

    @field_validator('url')
    @classmethod
    def permanent_url(cls, value):
        return saved_url(value)

    @model_validator(mode='after')
    def ownership_evidence(self):
        if self.ownership == 'marker' and not self.marker_seen:
            raise ValueError('Marker ownership requires a rendered marker')
        if self.ownership == 'bound_ids' and not (
            self.binding and self.binding.user_turn_id and self.binding.assistant_turn_id
            and self.url == self.binding.conversation_url
        ):
            raise ValueError('Bound ownership requires a saved URL and both logical IDs')
        if self.binding and self.url != self.binding.conversation_url:
            raise ValueError('Observation and binding URLs differ')
        if self.turn_ended and self.generating:
            raise ValueError('Generating and ended are contradictory observations')
        if self.projection_state == 'unresolved' and not self.projection_reason:
            raise ValueError('Unresolved projection requires a reason')
        if self.projection_state == 'snapshot' and not self.response_present:
            raise ValueError('Answer snapshot requires a present response')
        if self.projection_state == 'awaiting' and (
            self.response_present or self.turn_ended or self.markdown or self.text or self.blocks
            or self.links or self.unresolved_citations or self.terminal_reason
            or self.ownership != 'marker' or (self.binding and self.binding.assistant_turn_id)
            or self.projection_reason != 'awaiting_assistant'
        ):
            raise ValueError('Awaiting projection requires an unrendered initial answer')
        return self


class ObservationCheckpoint(StrictModel):
    schema_version: Literal[1] = 1
    turn: int = Field(ge=0)
    marker: str = Field(min_length=1)
    binding: TurnBinding | None = None
    accepted: Observation | None = None
    candidate: Observation | None = None
    unresolved_since: str | None = None
    rechecks: int = Field(default=0, ge=0, le=2)

    @field_validator('unresolved_since')
    @classmethod
    def timestamp(cls, value):
        if value is not None and datetime.fromisoformat(value).tzinfo is None:
            raise ValueError('Checkpoint timestamps require a timezone')
        return value

    @model_validator(mode='after')
    def pending_consistency(self):
        if (self.candidate is None) != (self.unresolved_since is None):
            raise ValueError('Candidate and reconciliation start must be saved together')
        if self.candidate is None and self.rechecks:
            raise ValueError('Recheck count without a candidate')
        if self.accepted and self.accepted.ownership == 'unverified':
            raise ValueError('Unowned response cannot be accepted')
        if self.accepted and self.accepted.projection_state == 'unresolved':
            raise ValueError('Unresolved response cannot be accepted')
        if self.candidate and self.candidate.projection_state != 'unresolved':
            raise ValueError('Candidate must describe unresolved evidence')
        for response in (self.accepted, self.candidate):
            if response and response.binding and self.binding:
                old, current = response.binding, self.binding
                if (old.conversation_url != current.conversation_url
                    or (old.user_turn_id and old.user_turn_id != current.user_turn_id)
                    or (old.assistant_turn_id and old.assistant_turn_id != current.assistant_turn_id)):
                    raise ValueError('Checkpoint response has conflicting turn ownership')
        return self


def parse_observation(value: dict) -> Observation:
    try:
        return Observation.model_validate(value)
    except (ValidationError, ValueError, TypeError, ResearchError) as exc:
        raise ResearchError('invalid_observation', 'Rendered observation failed validation') from exc


def read_checkpoint(raw: str | None, *, turn: int, marker: str,
                    expected_content_sha: str | None = None) -> ObservationCheckpoint:
    if raw is None:
        return ObservationCheckpoint(turn=turn, marker=marker)
    try:
        result = ObservationCheckpoint.model_validate(json.loads(raw))
        if result.turn != turn or result.marker != marker:
            raise ValueError('Checkpoint belongs to another research turn')
        if expected_content_sha is not None and (
            result.accepted is None or projection_fingerprint(result.accepted) != expected_content_sha
        ):
            raise ValueError('Checkpoint content differs from the durable hash')
        return result
    except (ValidationError, ValueError, TypeError, ResearchError) as exc:
        raise ResearchError('invalid_observation_checkpoint', 'Saved observation checkpoint is invalid') from exc


def projection_fingerprint(observation: Observation) -> str:
    return digest(json_bytes({
        'markdown': observation.markdown,
        'links': [link.model_dump() for link in observation.links],
        'unresolved_citations': observation.unresolved_citations,
    }))


def _strict_suffix(previous: list[str], current: list[str]) -> bool:
    # Exact semantic-block comparison, never a shorter-length acceptance gate.
    return bool(current) and len(current) < len(previous) and previous[-len(current):] == current


def advance_checkpoint(checkpoint: ObservationCheckpoint, observation: Observation,
                       *, at: datetime | None = None) -> ObservationCheckpoint:
    at = at or datetime.now(timezone.utc)
    if observation.ownership == 'unverified':
        return checkpoint
    previous = checkpoint.accepted
    binding = observation.binding or checkpoint.binding
    if checkpoint.binding and binding:
        old = checkpoint.binding
        if (old.conversation_url != binding.conversation_url
            or (old.user_turn_id and old.user_turn_id != binding.user_turn_id)
            or (old.assistant_turn_id and old.assistant_turn_id != binding.assistant_turn_id)):
            raise ResearchError('observation_binding_changed', 'Saved logical ownership changed')
    reason = observation.projection_reason if observation.projection_state == 'unresolved' else None
    if previous and previous.markdown and not observation.markdown:
        reason = reason or 'answer_disappeared'
    if previous and _strict_suffix(previous.blocks, observation.blocks):
        reason = reason or 'answer_prefix_missing'
    if checkpoint.candidate and previous and (previous.markdown.strip() or previous.blocks):
        recovered = bool(previous.blocks and observation.blocks
                         and previous.blocks[0] == observation.blocks[0]
                         and not _strict_suffix(previous.blocks, observation.blocks))
        if not recovered:
            reason = reason or checkpoint.candidate.projection_reason or 'projection_unresolved'
    values = checkpoint.model_dump()
    values['binding'] = binding.model_dump() if binding else None
    if reason:
        candidate = observation.model_copy(update={
            'projection_state': 'unresolved', 'projection_reason': reason,
        })
        values.update(candidate=candidate.model_dump(),
                      unresolved_since=checkpoint.unresolved_since or at.isoformat(),
                      rechecks=min(2, checkpoint.rechecks + 1) if checkpoint.candidate else 0)
    else:
        # UI end/generation evidence is refreshed even when semantic content is unchanged.
        values.update(accepted=observation.model_dump(), candidate=None,
                      unresolved_since=None, rechecks=0)
    return ObservationCheckpoint.model_validate(values)


def unresolved_expired(checkpoint: ObservationCheckpoint, *, at: datetime | None = None) -> bool:
    if not checkpoint.candidate:
        return False
    at = at or datetime.now(timezone.utc)
    return checkpoint.rechecks >= 2 or (
        at - datetime.fromisoformat(checkpoint.unresolved_since)
    ).total_seconds() >= 30
