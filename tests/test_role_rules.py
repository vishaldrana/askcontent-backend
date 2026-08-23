"""Role rules — the predicate three screens share."""

from askcontent.domain.role_rules import RoleRule, decide

CRAWLED = ("crawled", "public")


def test_no_rules_means_the_whole_corpus():
    """Denying by default would make every newly created role silently useless."""
    assert decide((), space="HELP", labels=CRAWLED).allowed


def test_deny_wins_over_allow():
    """Otherwise the safety of a configuration depends on the order somebody
    happened to type the rules in."""
    rules = (
        RoleRule("allow", space="HELP"),
        RoleRule("deny", label="crawled"),
    )
    assert not decide(rules, space="HELP", labels=CRAWLED).allowed


def test_an_allow_list_is_exhaustive_once_present():
    """"allow space=PUBLIC" is how an administrator says "only that". If allows
    were additive it would grant nothing new and the role would still see
    everything."""
    rules = (RoleRule("allow", space="PUBLIC"),)
    assert decide(rules, space="PUBLIC", labels=()).allowed
    assert not decide(rules, space="INTERNAL", labels=()).allowed


def test_a_rule_naming_both_requires_both():
    """It names a narrower thing than either alone; reading it as "or" would
    deny far more than was written."""
    rules = (RoleRule("deny", space="HELP", label="secret"),)
    assert not decide(rules, space="HELP", labels=("secret",)).allowed
    assert decide(rules, space="HELP", labels=("public",)).allowed
    assert decide(rules, space="OTHER", labels=("secret",)).allowed


def test_a_rule_naming_nothing_matches_nothing():
    """A half-filled form must not become a corpus-wide deny."""
    assert decide((RoleRule("deny"),), space="HELP", labels=CRAWLED).allowed


def test_the_reason_names_the_rule_that_decided():
    verdict = decide((RoleRule("deny", label="crawled"),), space="HELP", labels=CRAWLED)
    assert not verdict.allowed
    assert "crawled" in verdict.reason
