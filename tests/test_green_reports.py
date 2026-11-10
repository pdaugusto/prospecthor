"""Read-only reporting/export semantics. Never loads production credentials."""
import os
os.environ["PYTHON_DOTENV_DISABLED"]="1"
os.environ["DATABASE_URL"]=""
os.environ.setdefault("FLASK_SECRET_KEY","isolated-report-tests-only")
import csv
import io
import unittest
from datetime import datetime,timedelta
from unittest.mock import patch
from api import index as web
from api.green_ui import metrics,lead_summary,outreach_drafts
from src.lead_metrics import REPORT_ZONE,select_report_period,received_at,lead_status,public_signals,csv_safe_cell


class GreenReportTests(unittest.TestCase):
    def setUp(self):
        web.app.config.update(TESTING=True)
        self.client=web.app.test_client()
        self.now=datetime(2026,11,10,12,tzinfo=REPORT_ZONE)

    def login(self,admin=False):
        with self.client.session_transaction() as session:
            session.update(logged_in=True,user_id=1 if admin else 7,username="patrao" if admin else "cliente",role="admin" if admin else "client")

    def test_calendar_windows_assignment_priority_and_unknown_dates(self):
        leads=[{"id":1,"assigned_at":"2026-11-10T01:00:00","created_at":"2026-01-01"},
               {"id":2,"assigned_at":"2026-11-09T23:59:00"},
               {"id":3,"created_at":"2026-11-01"},
               {"id":4,"assigned_at":"invalid","scraped_at":"2026-11-10"},
               {"id":5,"assigned_at":"2026-11-11"}, {"id":6}]
        today=select_report_period(leads,"daily",self.now)
        self.assertEqual([item["id"] for item in today["leads"]],[1,4])
        self.assertEqual(today["unknown_dates"],1)
        self.assertEqual(len(select_report_period(leads,"weekly",self.now)["leads"]),3)
        self.assertEqual(len(select_report_period(leads,"monthly",self.now)["leads"]),4)
        self.assertEqual(today["start_date"],"2026-11-10")
        with self.assertRaises(ValueError): select_report_period(leads,"invalid",self.now)

    def test_aware_timestamps_are_converted_to_brazil_calendar_day(self):
        self.assertEqual(received_at({"assigned_at":"2026-11-10T02:30:00Z"}).date().isoformat(),"2026-11-09")

    def test_free_text_notes_do_not_erase_the_saved_status(self):
        self.assertEqual(lead_status({"contact_status":"convertido","notes":"Retomar na terça"}),"convertido")
        self.assertEqual(lead_status({"notes":"Convertido"}),"convertido")

    def test_chart_axis_handles_one_and_thirty_days(self):
        self.assertEqual(len(metrics([],1)["axis_labels"]),1)
        self.assertEqual(metrics([],1)["area"],"")
        self.assertEqual(len(metrics([],30)["series"]),30)
        self.assertEqual(len(metrics([],30)["axis_labels"]),7)

    def test_csv_formula_cells_are_text_and_numbers_are_preserved(self):
        for value in ["=1+1","+5511999991111","-2+3","@SUM(A1)","  =HYPERLINK(x)","\tformula"]:
            self.assertEqual(csv_safe_cell(value),"'"+value)
        self.assertEqual(csv_safe_cell(88),88)
        self.assertEqual(csv_safe_cell("Empresa"),"Empresa")

    def test_public_summary_uses_actual_site_and_excludes_score_metadata(self):
        lead={"name":"Empresa","lead_score":88,"website":"https://empresa.example","lead_problems":'["Sem site", "Blocos /100: dor=3 cap=4"]'}
        self.assertEqual(public_signals(lead["lead_problems"]),["Sem site"])
        summary=lead_summary(lead)
        self.assertIn("https://empresa.example",summary)
        self.assertNotIn("Blocos",summary)
        self.assertNotIn("Avanti",outreach_drafts(lead)["whatsapp"])

    def test_reports_and_csv_use_assignment_date_and_current_status(self):
        self.login()
        now=datetime.now(REPORT_ZONE)
        record={"name":"=FORMULA()","assigned_at":now.isoformat(),"created_at":(now-timedelta(days=100)).isoformat(),
                "contact_status":"convertido","notes":"Conversa anotada","lead_problems":"[]"}
        with patch.object(web,"get_all_leads",return_value=[record,{}]) as query:
            report=self.client.get("/api/reports/daily").get_json()
            exported=self.client.get("/api/export/csv?period=daily")
        self.assertEqual(report["total"],1)
        self.assertEqual(report["unknown_dates"],1)
        self.assertEqual(report["conversao_taxa"],100)
        rows=list(csv.reader(io.StringIO(exported.get_data(as_text=True)),delimiter=";"))
        self.assertEqual(rows[1][0],"'=FORMULA()")
        self.assertEqual(rows[1][6],"convertido")
        self.assertEqual(len(rows),2)
        self.assertEqual(query.call_args.kwargs["scope"],"own")
        self.assertEqual(query.call_args.kwargs["owner_id"],7)

    def test_audit_final_date_includes_the_whole_day(self):
        self.login(True)
        with patch("src.audit.ensure_schema"),patch("src.audit.query_logs",return_value=[]) as query:
            response=self.client.get("/api/audit?since=2026-11-09&until=2026-11-10&username=cliente&action=login&limit=50")
        self.assertEqual(response.status_code,200)
        self.assertEqual(query.call_args.kwargs["until"],"2026-11-10T23:59:59.999999")
        self.assertEqual(query.call_args.kwargs["since"],"2026-11-09T00:00:00")
        self.assertEqual(query.call_args.kwargs["username"],"cliente")

    def test_invalid_audit_filters_and_periods_are_rejected(self):
        self.login(True)
        for query in ["limit=0","limit=501","lead_id=bad","since=2026-11-10&until=2026-11-01","until=bad"]:
            with self.subTest(query=query):
                self.assertEqual(self.client.get("/api/audit?"+query).status_code,400)
        with patch.object(web,"get_all_leads",return_value=[]):
            self.assertEqual(self.client.get("/api/reports/unknown").status_code,400)
            self.assertEqual(self.client.get("/api/export/csv?period=unknown").status_code,400)


if __name__=="__main__": unittest.main()
