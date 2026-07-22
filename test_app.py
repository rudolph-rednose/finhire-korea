import unittest
from unittest.mock import patch

import app


class JobflexFeedTests(unittest.TestCase):
    @patch("app.fetch_post_json")
    def test_only_currently_submittable_jobs_are_returned(self, post):
        post.return_value = {"list": [
            {"positionSn": 1, "title": "현재 공고", "submissionStatus": "IN_SUBMISSION",
             "startDateTime": "2026-07-20T09:00:00", "endDateTime": "2026-08-01T18:00:00",
             "careerType": "CAREER", "classificationCode": "디지털"},
            {"positionSn": 2, "title": "마감 공고", "submissionStatus": "POST_SUBMISSION",
             "startDateTime": "2026-06-01T09:00:00", "endDateTime": "2026-06-15T18:00:00"},
        ]}
        with patch.object(app, "TODAY", app.dt.date(2026, 7, 23)):
            rows = app.fetch_jobflex_records("https://example.recruiter.co.kr")
        self.assertEqual(["현재 공고"], [row["title"] for row in rows])
        self.assertEqual("https://example.recruiter.co.kr/career/jobs/1", rows[0]["url"])
        self.assertEqual("example.recruiter.co.kr", post.call_args.args[2]["prefix"])


if __name__ == "__main__":
    unittest.main()
