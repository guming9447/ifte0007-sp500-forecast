"""Synthetic source controls; no internet and no real market observations."""
import argparse
import copy
import io
import json
import tempfile
import unittest
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from datetime import timedelta, time

import final_day_pipeline as f
import m1_forecast as m1


class SourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.days = [date(2026, 10, n) for n in (19,20,21,22,23)]
        self.old = {d: Decimal(100+i) for i,d in enumerate(self.days[:3])}
        self.new = {d: Decimal(100+i) for i,d in enumerate(self.days[:4])}
        self.b = self.days[-1]

    def test_append(self):
        self.assertEqual(f.merge(self.old,self.new,self.days,self.b,"S"),self.new)

    def test_trimmed_left_edge_retains_frozen_history(self):
        self.assertEqual(f.merge(self.old,{self.days[3]:Decimal(103)},self.days,self.b,"S"),self.new)

    def test_revision_less_than_cent_stops(self):
        self.new[self.days[0]] += Decimal(".0001")
        with self.assertRaisesRegex(ValueError,"REVISION"):
            f.merge(self.old,self.new,self.days,self.b,"S")

    def test_interior_disappearance_stops(self):
        del self.new[self.days[1]]
        with self.assertRaisesRegex(ValueError,"DISAPPEARED"):
            f.merge(self.old,self.new,self.days,self.b,"S")

    def test_missing_increment_stops(self):
        with self.assertRaisesRegex(ValueError,"GAP"):
            f.merge(self.old,self.old,self.days,self.b,"S")

    def test_future_is_not_merged(self):
        self.new[date(2027,1,1)]=Decimal(999)
        self.assertEqual(f.merge(self.old,self.new,self.days,self.b,"S"),{**self.old,self.days[3]:Decimal(103)})

    def test_non_session_value_stops(self):
        self.new[date(2026,10,18)]=Decimal(100)
        with self.assertRaisesRegex(ValueError,"NONSESSION"):
            f.merge(self.old,self.new,self.days,self.b,"S")

    def test_roundtrip_precision(self):
        path=self.root/"test.csv"
        values={self.days[0]:Decimal("100.123456789012345")}
        f.write_daily(path,"SP500",self.days,values)
        self.assertEqual(f.daily(path,"SP500"),values)

    def test_duplicate(self):
        path=self.root/"test.csv"
        path.write_text("observation_date,SP500\n2026-10-19,1\n2026-10-19,2\n")
        with self.assertRaises(ValueError): f.daily(path,"SP500")

    def test_nonfinite_nonpositive(self):
        for v in ("NaN","Infinity","0","-1"):
            path=self.root/"bad.csv"
            path.write_text("observation_date,SP500\n2026-10-19,"+v+"\n")
            with self.subTest(value=v), self.assertRaises(ValueError): f.daily(path,"SP500")

    def evidence(self):
        return {"retrieved_utc":"2026-11-13T15:00:00Z","available_before_utc":"2026-11-13T11:00:00Z",
                "review_status":"verified","reviewer":"TEST_ONLY","basis":"synthetic publication evidence",
                "url":"https://example.org","evidence_path":"test.txt","evidence_sha256":"a"*64}

    def test_late_acquisition_with_evidence_allowed(self):
        f.check_evidence(self.evidence(),f.utc("2026-11-13T12:00:00Z"),f.utc("2026-11-13T16:00:00Z"))

    def test_late_publication_stops(self):
        r=self.evidence(); r["available_before_utc"]="2026-11-13T12:00:00Z"
        with self.assertRaises(ValueError): f.check_evidence(r,f.utc("2026-11-13T12:00:00Z"),f.utc("2026-11-13T16:00:00Z"))

    def test_no_evidence_stops(self):
        r=self.evidence(); r.pop("evidence_path")
        with self.assertRaises(ValueError): f.check_evidence(r,f.utc("2026-11-13T12:00:00Z"),f.utc("2026-11-13T16:00:00Z"))

    def test_future_retrieval_stops(self):
        with self.assertRaises(ValueError): f.check_evidence(self.evidence(),f.utc("2026-11-13T12:00:00Z"),f.utc("2026-11-13T14:00:00Z"))

    def test_unreviewed_evidence_stops(self):
        r=self.evidence(); r["review_status"]="pending"
        with self.assertRaises(ValueError): f.check_evidence(r,f.utc("2026-11-13T12:00:00Z"),f.utc("2026-11-13T16:00:00Z"))

    def test_timezone_required(self):
        with self.assertRaises(ValueError): f.utc("2026-11-13T11:00:00")

    def test_input_hash_tamper(self):
        p=self.root/"x"; p.write_text("synthetic")
        with self.assertRaises(ValueError): f.bind_file({"path":"x","sha256":"0"*64},self.root)

    def test_training_hash_definition(self):
        import hashlib
        self.assertEqual(f.training_hash(self.days),hashlib.sha256(b"2026-10-19,2026-10-20,2026-10-21,2026-10-22,2026-10-23").hexdigest())

    def pair(self):
        dates=[str(d) for d in self.days]
        a={"P0":100,"origin":"2026-11-06","input_hashes":{"SP500.csv":"s","VIXCLS.csv":"v"},
           "train_origins_sha256":f.training_hash(dates),"default":{"n_train":5,"train_origins":dates,
           "forecast_full_precision":100.25,"forecast_2dp":"100.25","uses_vrp_sample_membership":True}}
        b={"status":"OK","input_sha256":{"canonical_spx":"s","canonical_vix":"v"},"deployed_W260":{
           "P0":100,"origin":"2026-11-06","n_rows":5,"train_origins":dates,
           "train_origins_sha256":f.training_hash(dates),"F_full":100.25,"F_2dp":"100.25","row_set":"VRP-eligible"}}
        return a,b

    def test_independent_match(self):
        self.assertTrue(all(f.compare(*self.pair()).values()))

    def test_different_branch_rejected_even_same_number(self):
        a,b=self.pair(); b["deployed_W260"]["row_set"]="labelled"
        self.assertFalse(all(f.compare(a,b).values()))

    def test_different_inputs_rejected(self):
        a,b=self.pair(); b["input_sha256"]["canonical_spx"]="changed"
        self.assertFalse(all(f.compare(a,b).values()))

    def test_numeric_tolerance_not_override_rounding(self):
        a,b=self.pair(); b["deployed_W260"]["F_full"]+=.001; b["deployed_W260"]["F_2dp"]="100.26"
        self.assertFalse(all(f.compare(a,b).values()))


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        start=date(2020,1,1)
        self.days=[start+timedelta(days=i) for i in range((date(2026,11,20)-start).days+1)
                   if (start+timedelta(days=i)).weekday()<5]
        self.b=date(2026,11,5)
        self.sp={d:Decimal("100")+Decimal(i)/100 for i,d in enumerate(self.days) if d<=self.b}
        self.vix={d:Decimal(20+i%11) for i,d in enumerate(self.days) if d<=self.b}
        self.cfg={"origin":"2026-11-06","historical_availability_assumption_acknowledged":True,
                  "source_policy":f.SOURCE_POLICY,
                  "inputs":{},"availability":{}}
        for role,col,values in (("frozen_sp","SP500",dict(list(self.sp.items())[:-10])),
                               ("frozen_vix","VIXCLS",dict(list(self.vix.items())[:-10])),
                               ("sp_crosscheck","SP500",self.sp),("vix_history","VIXCLS",self.vix),
                               ("sp_official","SP500",dict(list(self.sp.items())[5:])),
                               ("vix_official","VIXCLS",{self.b:self.vix[self.b]})):
            p=self.root/(role+".csv"); f.write_daily(p,col,sorted(values),values)
            self.cfg["inputs"][role]={"path":p.name,"sha256":m1.sha(p)}
        for role,days in (("calendar",self.days),("frozen_calendar",[d for d in self.days if d<=self.b])):
            p=self.root/(role+".csv")
            p.write_text("session_date,close_utc\n"+"".join(str(d)+","+datetime.combine(d,time(16),m1.NY).isoformat()+"\n" for d in days))
            self.cfg["inputs"][role]={"path":p.name,"sha256":m1.sha(p)}
        ep=self.root/"synthetic_evidence.txt"; ep.write_text("SYNTHETIC TEST ONLY; invented publication record")
        for role in ("vix_history","sp_official","vix_official"):
            self.cfg["availability"][role]={"retrieved_utc":"2026-11-07T10:00:00Z",
                "available_before_utc":"2026-11-05T22:00:00Z","review_status":"verified",
                "reviewer":"SYNTHETIC TEST","basis":"invented","url":"https://example.org/synthetic",
                "input_sha256":self.cfg["inputs"][role]["sha256"],"evidence_path":ep.name,"evidence_sha256":m1.sha(ep),
                "raw_role":role}
        self.baselines={key:("synthetic",self.cfg["inputs"][role]["sha256"])
                        for key,role in (("SP500","frozen_sp"),("VIXCLS","frozen_vix"),("calendar","frozen_calendar"))}
        class Clock(datetime):
            @classmethod
            def now(cls,tz=None): return datetime(2026,11,7,12,tzinfo=timezone.utc)
        self.clock=Clock

    def lock(self):
        p=self.root/"config.json"; p.write_text(json.dumps(self.cfg))
        with patch.object(m1,"INPUTS",self.baselines),patch.object(f,"datetime",self.clock),patch.object(f,"SP_OFFICIAL_START",self.days[5]):
            f.lock(p,self.root/"locked")

    def test_end_to_end_candidate_and_hashes(self):
        self.lock(); f.calculate(self.root/"locked",self.root/"candidate")
        r=json.loads((self.root/"candidate/candidate.json").read_text())
        self.assertFalse(r["final_forecast_approved"])
        self.assertEqual(r["baseline"],"2026-11-05")
        self.assertEqual(r["target"],"2026-11-13")
        self.assertEqual(r["default"]["n_train"],260)

    def test_missing_current_vix_fallback(self):
        self.cfg["inputs"].pop("vix_official"); self.cfg["availability"].pop("vix_official")
        self.cfg["missing_current_vix_reason"]="SYNTHETIC unavailable"
        self.lock(); f.calculate(self.root/"locked",self.root/"candidate")
        r=json.loads((self.root/"candidate/candidate.json").read_text())
        self.assertFalse(r["default"]["uses_vrp_sample_membership"])

    def test_missing_current_vix_without_reason_stops(self):
        self.cfg["inputs"].pop("vix_official"); self.cfg["availability"].pop("vix_official")
        with self.assertRaises(ValueError): self.lock()

    def test_official_override_after_review(self):
        values=f.daily(self.root/"sp_official.csv","SP500")
        values[self.b]=Decimal(500)
        self.replace_sp("sp_official",values)
        self.review(self.b)
        self.lock()
        self.assertEqual(f.daily(self.root/"locked/SP500.csv","SP500")[self.b],Decimal(500))

    def test_unreviewed_official_conflict_stops(self):
        values=f.daily(self.root/"sp_official.csv","SP500")
        values[self.b]=Decimal(500)
        self.replace_sp("sp_official",values)
        with self.assertRaisesRegex(ValueError,"SP_CROSSCHECK_CONFLICT"): self.lock()

    def replace_sp(self,role,values):
        p=self.root/(role+".csv"); p.unlink()
        f.write_daily(p,"SP500",sorted(values),values)
        self.cfg["inputs"][role]["sha256"]=m1.sha(p)
        if role in self.cfg["availability"]:
            self.cfg["availability"][role]["input_sha256"]=m1.sha(p)

    def review(self,day):
        self.cfg.setdefault("sp_difference_reviews",{})[str(day)]={
            "reviewer":"SYNTHETIC TEST", "basis":"invented investigation",
            "resolution":"use_official",
            "official":str(f.daily(self.root/"sp_official.csv","SP500")[day]),
            "crosscheck":str(f.daily(self.root/"sp_crosscheck.csv","SP500")[day]),
            "official_sha256":self.cfg["inputs"]["sp_official"]["sha256"],
            "crosscheck_sha256":self.cfg["inputs"]["sp_crosscheck"]["sha256"]}

    def test_official_missing_increment_stops_even_when_fred_has_value(self):
        values=f.daily(self.root/"sp_official.csv","SP500")
        del values[self.b-timedelta(days=1)]
        self.replace_sp("sp_official",values)
        with self.assertRaisesRegex(ValueError,"OFFICIAL_SP_GAP"): self.lock()

    def test_official_missing_p0_stops(self):
        values=f.daily(self.root/"sp_official.csv","SP500")
        del values[self.b]
        self.replace_sp("sp_official",values)
        with self.assertRaisesRegex(ValueError,"OFFICIAL_SP_GAP"): self.lock()

    def test_fred_missing_recent_days_does_not_stop(self):
        self.replace_sp("sp_crosscheck",dict(list(self.sp.items())[:-5]))
        self.lock()
        self.assertEqual(f.daily(self.root/"locked/SP500.csv","SP500"),self.sp)

    def test_no_fred_crosscheck_is_reported_not_used_for_filling(self):
        self.cfg["inputs"].pop("sp_crosscheck")
        self.lock()
        checks=json.loads((self.root/"locked/source_comparisons.json").read_text())
        self.assertTrue(any(c.get("status")=="FRED_MISSING_NOT_INPUT" for c in checks))

    def test_historical_increment_conflict_stops_until_reviewed(self):
        day=self.b-timedelta(days=1)
        values=dict(self.sp); values[day]+=Decimal(1)
        self.replace_sp("sp_crosscheck",values)
        with self.assertRaisesRegex(ValueError,"SP_CROSSCHECK_CONFLICT"): self.lock()
        self.review(day)
        self.lock()
        self.assertEqual(f.daily(self.root/"locked/SP500.csv","SP500")[day],self.sp[day])

    def test_review_does_not_carry_to_another_input_version(self):
        values=dict(self.sp); values[self.b]+=Decimal(1)
        self.replace_sp("sp_crosscheck",values)
        self.review(self.b)
        values[self.b]+=Decimal(1)
        self.replace_sp("sp_crosscheck",values)
        with self.assertRaisesRegex(ValueError,"SP_CROSSCHECK_CONFLICT"): self.lock()

    def test_subcent_sp_crosscheck_difference_does_not_change_inputs(self):
        values=dict(self.sp); values[self.b]+=Decimal(".0001")
        self.replace_sp("sp_crosscheck",values)
        self.lock()
        self.assertEqual(f.daily(self.root/"locked/SP500.csv","SP500"),self.sp)

    def test_official_old_history_does_not_silently_replace_frozen(self):
        values=f.daily(self.root/"sp_official.csv","SP500")
        values[min(values)]+=Decimal(1)
        self.replace_sp("sp_official",values)
        with self.assertRaisesRegex(ValueError,"FROZEN_OFFICIAL_CONFLICT"): self.lock()

    def test_new_rows_provenance_is_official_not_fred(self):
        self.lock()
        rows=json.loads((self.root/"locked/row_provenance.json").read_text())
        start=self.days[5]
        for row in rows:
            if row["series"]=="SP500":
                self.assertEqual(row["source_role"],"frozen_sp" if row["date"]<str(start) else "sp_official")

    def test_official_old_interior_gap_cannot_be_filled_from_frozen(self):
        values=f.daily(self.root/"sp_official.csv","SP500")
        del values[self.days[100]]
        self.replace_sp("sp_official",values)
        with self.assertRaisesRegex(ValueError,"OFFICIAL_SP_GAP"): self.lock()

    def test_rolling_left_edge_cannot_expand_frozen_prefix(self):
        values=f.daily(self.root/"sp_official.csv","SP500")
        del values[self.days[5]]
        self.replace_sp("sp_official",values)
        with self.assertRaisesRegex(ValueError,"OFFICIAL_SP_GAP"): self.lock()

    def test_frozen_prefix_retained_with_full_official_overlap(self):
        self.lock()
        rows=json.loads((self.root/"locked/row_provenance.json").read_text())
        kept=[r for r in rows if r["series"]=="SP500" and r["source_role"]=="frozen_sp"]
        self.assertEqual([r["date"] for r in kept],[str(d) for d in self.days[:5]])

    def test_subcent_official_historical_revision_stops(self):
        values=f.daily(self.root/"sp_official.csv","SP500")
        values[self.days[100]]+=Decimal(".0001")
        self.replace_sp("sp_official",values)
        with self.assertRaisesRegex(ValueError,"FROZEN_OFFICIAL_CONFLICT"): self.lock()

    def test_old_source_control_requires_explicit_migration(self):
        self.cfg.pop("source_policy")
        with self.assertRaisesRegex(ValueError,"D031"): self.lock()

    def test_final_origin_missing_nov10_or_nov11_stops(self):
        self.cfg["origin"]="2026-11-13"
        self.b=date(2026,11,12)
        sp={d:Decimal(200) for d in self.days if date(2026,11,5)<d<=self.b}
        for role in ("sp_official","sp_crosscheck"):
            values=f.daily(self.root/(role+".csv"),"SP500") | sp
            self.replace_sp(role,values)
        for role in ("vix_history","vix_official"):
            p=self.root/(role+".csv")
            values=f.daily(p,"VIXCLS") | {d:Decimal(20) for d in sp}
            p.unlink(); f.write_daily(p,"VIXCLS",sorted(values),values)
            self.cfg["inputs"][role]["sha256"]=m1.sha(p)
            self.cfg["availability"][role]["input_sha256"]=m1.sha(p)
        for record in self.cfg["availability"].values():
            record["retrieved_utc"]="2026-11-13T10:00:00Z"
            record["available_before_utc"]="2026-11-12T22:00:00Z"
        class FinalClock(datetime):
            @classmethod
            def now(cls,tz=None): return datetime(2026,11,13,11,tzinfo=timezone.utc)
        self.clock=FinalClock
        complete=f.daily(self.root/"sp_official.csv","SP500")
        for day in (date(2026,11,10),date(2026,11,11)):
            values=dict(complete); del values[day]
            self.replace_sp("sp_official",values)
            with self.subTest(day=day), self.assertRaisesRegex(ValueError,"OFFICIAL_SP_GAP"):
                self.lock()

    def test_vix_subcent_revision_still_stops(self):
        p=self.root/"vix_history.csv"
        values=f.daily(p,"VIXCLS"); values[min(values)]+=Decimal(".0001")
        p.unlink(); f.write_daily(p,"VIXCLS",sorted(values),values)
        self.cfg["inputs"]["vix_history"]["sha256"]=m1.sha(p)
        self.cfg["availability"]["vix_history"]["input_sha256"]=m1.sha(p)
        with self.assertRaisesRegex(ValueError,"HISTORY_REVISION"): self.lock()

    def test_locked_tamper_stops(self):
        self.lock()
        (self.root/"locked/SP500.csv").write_text("changed")
        with self.assertRaisesRegex(ValueError,"changed"):
            f.calculate(self.root/"locked",self.root/"candidate")

    def test_no_overwrite(self):
        self.lock()
        with self.assertRaises(FileExistsError): self.lock()


if __name__ == "__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args()
    f.ensure_e(args.output)
    args.output.mkdir(parents=True,exist_ok=False)
    stream=io.StringIO()
    suite=unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromTestCase(c) for c in (SourceTests,PipelineTests))
    result=unittest.TextTestRunner(stream=stream,verbosity=2).run(suite)
    (args.output/"tests.txt").write_text(stream.getvalue(),encoding="utf-8")
    m1.save(args.output/"summary.json",{"tests":result.testsRun,"passed":result.wasSuccessful(),"synthetic_only":True})
    print(stream.getvalue())
    raise SystemExit(0 if result.wasSuccessful() else 1)
