from app.orchestrator.agents.supervisor import _enable_not_launch

RUN = [{"action": "project.status"}, {"action": "step.run_step"}]


def test_enable_word_is_not_launch():
    assert _enable_not_launch("хотя нет, предметы всё-таки включи", RUN)
    assert _enable_not_launch("включи обратно ноду предметы", RUN)


def test_real_launch_untouched():
    assert not _enable_not_launch("включи и запусти предметы", RUN)
    assert not _enable_not_launch("сгенерируй предметы", RUN)
    assert not _enable_not_launch("включи предметы", [{"action": "node.set_disabled"}])
