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
