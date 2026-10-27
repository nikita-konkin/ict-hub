from app.models import FeedbackReport


def test_operator_can_submit_feedback(operator_client, db, operator_user):
    response = operator_client.post(
        "/feedback",
        data={
            "category": "bug",
            "message": "Something broke on the dashboard.",
            "page_url": "http://testserver/",
        },
    )
    assert response.status_code == 200

    reports = db.query(FeedbackReport).all()
    assert len(reports) == 1
    assert reports[0].user_id == operator_user.id
    assert reports[0].category == "bug"
    assert reports[0].status == "new"


def test_operator_cannot_view_feedback_admin(operator_client):
    response = operator_client.get("/feedback")
    assert response.status_code == 403


def test_admin_can_view_feedback_admin(admin_client, db, operator_user):
    db.add(
        FeedbackReport(
            user_id=operator_user.id,
            category="idea",
            message="Add a quick search on the dashboard.",
            page_url="http://testserver/",
            status="new",
        )
    )
    db.commit()

    response = admin_client.get("/feedback")
    assert response.status_code == 200
    assert "Add a quick search on the dashboard." in response.text


def test_admin_can_mark_seen(admin_client, db, operator_user):
    report = FeedbackReport(
        user_id=operator_user.id,
        category="feedback",
        message="Looks good.",
        page_url="http://testserver/",
        status="new",
    )
    db.add(report)
    db.commit()
    db.refresh(report)

    response = admin_client.post(f"/feedback/{report.id}/seen", follow_redirects=False)
    assert response.status_code == 302

    db.refresh(report)
    assert report.status == "seen"


def test_admin_can_mark_unseen(admin_client, db, operator_user):
    report = FeedbackReport(
        user_id=operator_user.id,
        category="feedback",
        message="Was seen but needs attention again.",
        page_url="http://testserver/",
        status="seen",
    )
    db.add(report)
    db.commit()
    db.refresh(report)

    response = admin_client.post(f"/feedback/{report.id}/unseen", follow_redirects=False)
    assert response.status_code == 302

    db.refresh(report)
    assert report.status == "new"


def test_admin_can_download_xml(admin_client, db, operator_user):
    report = FeedbackReport(
        user_id=operator_user.id,
        category="bug",
        message="XML export test.",
        page_url="http://testserver/",
        status="new",
    )
    db.add(report)
    db.commit()
    db.refresh(report)

    response = admin_client.get(f"/feedback/{report.id}.xml")
    assert response.status_code == 200
    assert "application/xml" in response.headers.get("content-type", "")
    assert "<feedback_report" in response.text
    assert "XML export test." in response.text


def test_xml_export_stays_well_formed_with_control_characters(admin_client, db, operator_user):
    """A pasted terminal log carries ESC colour codes, which XML 1.0 cannot hold."""
    import xml.etree.ElementTree as ET

    report = FeedbackReport(
        user_id=operator_user.id,
        category="bug",
        message="\x1b[31mERROR\x1b[0m failed\x00 at step\x07 3\nnext line",
        page_url="http://testserver/\x01",
        user_agent="agent\x0b",
        status="new",
    )
    db.add(report)
    db.commit()
    db.refresh(report)

    response = admin_client.get(f"/feedback/{report.id}.xml")
    assert response.status_code == 200
    root = ET.fromstring(response.content)
    assert root.findtext("message") == "[31mERROR[0m failed at step 3\nnext line"
    assert root.findtext("page_url") == "http://testserver/"
    assert root.findtext("user_agent") == "agent"


def test_submit_caps_page_url_at_column_size(operator_client, db):
    response = operator_client.post(
        "/feedback",
        data={"message": "Long URL.", "page_url": "http://testserver/" + "x" * 5000},
        headers={"accept": "application/json"},
    )
    assert response.status_code == 200
    report = db.query(FeedbackReport).one()
    assert len(report.page_url) == 1024
