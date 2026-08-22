import afuture.directional as directional
import afuture.execution_aligned_policy as policy


def test_only_execution_aligned_policy_owns_frozen_signal_engine():
    assert not hasattr(directional, "FrozenAggressivePolicy")
    assert not hasattr(directional, "_FROZEN_TEMPLATE_IDS")
    assert not hasattr(directional, "_parse_template_id")
    assert not hasattr(directional, "_template_weight_path")
    assert not hasattr(directional, "_trailing_scores")
    assert hasattr(policy, "ExecutionAlignedAggressivePolicy")
    assert len(policy._EXECUTION_TEMPLATE_IDS) == 96
