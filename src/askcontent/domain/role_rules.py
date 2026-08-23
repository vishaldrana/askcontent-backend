"""What a role may see *of* a connector's corpus.

The connector's scope decides what the corpus is. These rules decide which part
of it a particular role reaches — which is what lets one connector serve two
audiences without maintaining two copies of the knowledgebase.

This module is *pure*, and that is the point. The same function is used by the
retrieval gate, by the effective-access screen and by Diagnose. Three
implementations of an access predicate is three predicates, and the divergence
shows up as a document the console swears is hidden and an answer that cites it
anyway.

Precedence, and why:

  * **Deny wins.** If any rule denies a document, it is denied, whatever else
    allows it. The alternative — last rule wins, or most specific wins — means
    the safety of a configuration depends on the order somebody typed it in.
  * **An allow list, once present, is exhaustive.** Adding "allow space=PUBLIC"
    is how an administrator says "this role sees only that". If allows were
    merely additive, that rule would grant nothing it did not already have and
    the role would still see everything, which is the opposite of what was
    written.
  * **No rules means no narrowing.** A role with no rules reaches the whole
    corpus, still subject to the store's own permissions. Denying by default
    would make every new role silently useless.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RoleRule:
    effect: str  # "allow" | "deny"
    space: str | None = None
    label: str | None = None

    def matches(self, *, space: str | None, labels: tuple[str, ...]) -> bool:
        """A rule with both a space and a label requires both — it names a
        narrower thing than either alone, and reading it as "or" would deny
        far more than was written."""
        if self.space is not None and space != self.space:
            return False
        if self.label is not None and self.label not in labels:
            return False
        # A rule naming neither matches nothing. It cannot be created through
        # the API, and treating it as "matches everything" would turn a
        # half-filled form into a corpus-wide deny.
        return self.space is not None or self.label is not None


@dataclass(frozen=True)
class RuleDecision:
    allowed: bool
    reason: str


def decide(
    rules: tuple[RoleRule, ...], *, space: str | None, labels: tuple[str, ...]
) -> RuleDecision:
    if not rules:
        return RuleDecision(True, "no role rules")

    for rule in rules:
        if rule.effect == "deny" and rule.matches(space=space, labels=labels):
            return RuleDecision(False, f"denied by rule on {_names(rule)}")

    allows = [r for r in rules if r.effect == "allow"]
    if not allows:
        return RuleDecision(True, "no allow list; not denied")

    for rule in allows:
        if rule.matches(space=space, labels=labels):
            return RuleDecision(True, f"allowed by rule on {_names(rule)}")

    return RuleDecision(False, "outside this role's allow list")


def _names(rule: RoleRule) -> str:
    parts = []
    if rule.space:
        parts.append(f"space {rule.space}")
    if rule.label:
        parts.append(f"label {rule.label}")
    return " and ".join(parts)
