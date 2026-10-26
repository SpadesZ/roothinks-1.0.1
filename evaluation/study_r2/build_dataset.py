# Roothinks source maintenance contract
# 目的：產生 Study R2 真模型評估的合成資料集 evaluation/study_r2/dataset.json（30 段、15 解釋、15 批判）。
# 來源：全部由 RootThinks 雲端工程代理為本評估撰寫；不取自任何論文、私人文件或 sealed holdout，可公開。
# 每段：前段／選段所在段／後段三段文字；選取句；必須保留的條件（key_conditions，可在任一段）；不得出現的過度宣稱（forbidden_claims）。
# 預先登記：本檔與 dataset.json 在任何模型執行之前 commit；之後不得為了結果修改條件或禁語。
# 執行：python evaluation/study_r2/build_dataset.py
import hashlib
import json
from pathlib import Path

R = [
("S01", "materials", "scope", "explain", "We studied thermal response in three alloy samples prepared by the same furnace.",
 "Sample A improved by 12 percent at 300 K under one calibration protocol. Samples B and C were not measured.",
 "Annealing time was held at 40 minutes for every sample.", "Sample A improved by 12 percent at 300 K under one calibration protocol.",
 ["Sample A", "300 K"], ["all samples", "all temperatures", "replicated"]),
("S02", "medicine", "correlation", "critique", "The cohort included 412 adults from two clinics in one city.",
 "Patients who walked more than 8000 steps per day had lower systolic blood pressure. The analysis was cross-sectional and did not adjust for diet.",
 "Medication use was recorded but not modelled.", "Patients who walked more than 8000 steps per day had lower systolic blood pressure.",
 ["cross-sectional", "8000 steps"], ["causes", "proves", "randomized"]),
("S03", "ecology", "negation", "explain", "Bird counts were taken at 20 wetland sites in spring.",
 "Nest density did not differ between restored and unrestored sites. The power to detect a 10 percent difference was 0.45.",
 "Sites were visited twice each season.", "Nest density did not differ between restored and unrestored sites.",
 ["did not differ", "0.45"], ["restoration increased", "significantly higher"]),
("S04", "computer science", "benchmark scope", "critique", "We evaluate the parser on two English news corpora.",
 "The model reached 91.2 F1 on the in-domain test set. Performance on clinical text was not evaluated.",
 "Training used a single random seed.", "The model reached 91.2 F1 on the in-domain test set.",
 ["in-domain", "91.2"], ["state of the art on all domains", "works on clinical text"]),
("S05", "psychology", "small sample", "explain", "Participants were 24 undergraduate volunteers.",
 "Reaction times were faster after the brief mindfulness session in this pilot sample. No control group was included.",
 "The session lasted ten minutes.", "Reaction times were faster after the brief mindfulness session in this pilot sample.",
 ["pilot", "No control group"], ["causal effect", "generalizes to adults"]),
("S06", "chemistry", "conditional", "critique", "Catalyst X was tested in aqueous solution.",
 "Yield increased to 78 percent only when the pH was kept between 6.5 and 7.0. Outside this range the yield fell below 30 percent.",
 "Temperature was fixed at 25 C.", "Yield increased to 78 percent only when the pH was kept between 6.5 and 7.0.",
 ["pH", "6.5 and 7.0"], ["at any pH", "universally"]),
("S07", "epidemiology", "hedge", "explain", "Surveillance data covered 2015 to 2019.",
 "Incidence may have declined after the vaccination campaign, but reporting practices also changed in 2017.",
 "Laboratory confirmation was available for half of the cases.",
 "Incidence may have declined after the vaccination campaign, but reporting practices also changed in 2017.",
 ["may have", "reporting practices"], ["the campaign caused", "definitely declined"]),
("S08", "physics", "measurement limit", "critique", "The detector was calibrated with a cesium source.",
 "No signal above background was observed between 2 and 5 GeV. The upper limit depends on the assumed efficiency of 0.8.",
 "Data were collected for 40 days.", "No signal above background was observed between 2 and 5 GeV.",
 ["upper limit", "0.8"], ["particle does not exist", "ruled out at all energies"]),
("S09", "agronomy", "site specificity", "explain", "Field trials ran for two seasons on one farm.",
 "The new cultivar yielded 9 percent more grain on loam soil under irrigation. Dryland plots were not included.",
 "Fertilizer rates followed local guidance.", "The new cultivar yielded 9 percent more grain on loam soil under irrigation.",
 ["loam soil", "irrigation"], ["all soils", "drought tolerant"]),
("S10", "neuroscience", "animal model", "critique", "Experiments used adult male mice.",
 "Blocking receptor R reduced anxiety-like behavior in the maze test. Female mice were not tested.",
 "Doses were chosen from a prior study.", "Blocking receptor R reduced anxiety-like behavior in the maze test.",
 ["mice", "maze test"], ["treats anxiety in humans", "in patients"]),
("S11", "economics", "observational", "explain", "We use administrative records from one region.",
 "Firms that adopted the subsidy hired more workers in the following year. Adoption was voluntary.",
 "We do not observe wages.", "Firms that adopted the subsidy hired more workers in the following year.",
 ["voluntary", "following year"], ["the subsidy caused hiring", "nationwide"]),
("S12", "materials", "duplicate quote", "critique", "Two coating methods were compared on steel plates.",
 "The coating reduced corrosion. After 30 days in salt spray the coating reduced corrosion by 40 percent relative to bare steel.",
 "Plates were rinsed before inspection.",
 "After 30 days in salt spray the coating reduced corrosion by 40 percent relative to bare steel.",
 ["30 days", "salt spray"], ["permanent protection", "all metals"]),
("S13", "genomics", "multiple testing", "explain", "We tested 20000 genetic variants.",
 "One variant was associated with height at p = 0.003 before correction. It did not pass the genome-wide threshold.",
 "Replication was not attempted.", "One variant was associated with height at p = 0.003 before correction.",
 ["before correction", "did not pass"], ["genome-wide significant", "was replicated"]),
("S14", "climate", "model dependence", "critique", "Projections use one regional climate model.",
 "Under the high-emission scenario, summer rainfall decreased by 15 percent by 2080. Other models were not compared.",
 "Bias correction used 1981 to 2010 observations.", "Under the high-emission scenario, summer rainfall decreased by 15 percent by 2080.",
 ["high-emission scenario", "one regional climate model"], ["will certainly decrease", "all models agree"]),
("S15", "education", "self-report", "explain", "Students completed an online survey.",
 "Students who used the tutoring app reported higher confidence in algebra. Test scores were not collected.",
 "Response rate was 38 percent.", "Students who used the tutoring app reported higher confidence in algebra.",
 ["reported", "Test scores were not collected"], ["improved test scores", "learned more"]),
("S16", "pharmacology", "dose range", "critique", "Drug D was given orally to healthy volunteers.",
 "Plasma levels rose linearly between 10 and 50 mg. Doses above 50 mg were not studied.",
 "Samples were taken over 24 hours.", "Plasma levels rose linearly between 10 and 50 mg.",
 ["10 and 50 mg", "not studied"], ["at all doses", "in patients"]),
("S17", "oceanography", "temporal scope", "explain", "Buoys recorded temperature every hour.",
 "Surface temperature rose by 0.3 C during the 2021 heatwave at the three northern buoys. Southern buoys failed in July.",
 "Salinity sensors were recalibrated in June.", "Surface temperature rose by 0.3 C during the 2021 heatwave at the three northern buoys.",
 ["2021 heatwave", "northern buoys"], ["long-term warming trend", "entire ocean"]),
("S18", "robotics", "simulation only", "critique", "The controller was designed for a quadruped robot.",
 "In simulation the controller recovered from pushes up to 40 N in 95 percent of trials. Hardware tests are planned.",
 "Simulation used rigid terrain.", "In simulation the controller recovered from pushes up to 40 N in 95 percent of trials.",
 ["In simulation", "40 N"], ["on the real robot", "on any terrain"]),
("S19", "public health", "subgroup", "explain", "The trial enrolled 600 smokers.",
 "Quit rates were higher with the app in participants under 35. In older participants there was no difference.",
 "Follow-up lasted six months.", "Quit rates were higher with the app in participants under 35.",
 ["under 35", "no difference"], ["all smokers", "long-term abstinence"]),
("S20", "astronomy", "detection threshold", "critique", "We searched archival images for faint companions.",
 "No companion brighter than magnitude 18 was found within 2 arcseconds. Fainter companions cannot be excluded.",
 "Seeing varied between nights.", "No companion brighter than magnitude 18 was found within 2 arcseconds.",
 ["magnitude 18", "2 arcseconds"], ["has no companion", "single star confirmed"]),
("S21", "materials", "negated mechanism", "explain", "Grain size was measured by electron microscopy.",
 "Hardness increased after rolling, but the increase was not explained by grain size, which stayed near 12 micrometres.",
 "Texture was not measured.",
 "Hardness increased after rolling, but the increase was not explained by grain size, which stayed near 12 micrometres.",
 ["not explained by grain size", "12 micrometres"], ["because grains became smaller", "grain refinement caused"]),
("S22", "sociology", "sampling", "critique", "Interviews were held in two neighborhoods.",
 "Most residents described the new park as safe. Residents who declined interviews were not followed up.",
 "Interviews lasted about 30 minutes.", "Most residents described the new park as safe.",
 ["described", "declined interviews"], ["the park reduced crime", "city-wide"]),
("S23", "immunology", "in vitro", "explain", "Cells were obtained from four donors.",
 "Compound Y reduced cytokine release in cultured macrophages at 5 micromolar. Toxicity was not assessed.",
 "Each condition was run in triplicate.", "Compound Y reduced cytokine release in cultured macrophages at 5 micromolar.",
 ["cultured macrophages", "5 micromolar"], ["is safe", "in patients", "anti-inflammatory drug"]),
("S24", "hydrology", "calibration period", "critique", "The model was calibrated on one river basin.",
 "Simulated peak flows matched observations within 8 percent for the 2010 to 2015 period. Validation on later years was not done.",
 "Snowmelt was represented by a degree-day method.",
 "Simulated peak flows matched observations within 8 percent for the 2010 to 2015 period.",
 ["2010 to 2015", "Validation on later years was not done"], ["was validated", "predicts future floods"]),
("S25", "nutrition", "short duration", "explain", "Volunteers followed the diet for two weeks.",
 "Fasting glucose fell slightly on the high-fiber diet, but the change was within day-to-day variation.",
 "Body weight was stable.", "Fasting glucose fell slightly on the high-fiber diet, but the change was within day-to-day variation.",
 ["slightly", "day-to-day variation"], ["prevents diabetes", "clinically significant"]),
("S26", "computer security", "threat model", "critique", "We analyzed one open-source library.",
 "The fuzzing campaign found no memory errors in 72 hours on the default build. Optional modules were disabled.",
 "We used a single fuzzer.", "The fuzzing campaign found no memory errors in 72 hours on the default build.",
 ["72 hours", "default build"], ["is secure", "free of vulnerabilities"]),
("S27", "zoology", "observation count", "explain", "Field notes cover one breeding season.",
 "Tool use was observed twice, both times in the same adult female. Juveniles were not seen using tools.",
 "Observation hours were limited by weather.", "Tool use was observed twice, both times in the same adult female.",
 ["twice", "same adult female"], ["the species uses tools", "common behavior"]),
("S28", "materials", "unit sensitivity", "critique", "Films were deposited at 10² Pa.",
 "Films grown at 10² Pa showed lower resistivity than films grown at 10³ Pa. Thickness differed between the two sets.",
 "Resistivity was measured at room temperature.", "Films grown at 10² Pa showed lower resistivity than films grown at 10³ Pa.",
 ["10² Pa", "Thickness differed"], ["pressure alone", "102 Pa"]),
("S29", "cardiology", "surrogate outcome", "explain", "The trial randomized 200 patients.",
 "The drug lowered LDL cholesterol by 25 percent at 12 weeks. Cardiovascular events were not an outcome.",
 "Adherence was 90 percent.", "The drug lowered LDL cholesterol by 25 percent at 12 weeks.",
 ["12 weeks", "Cardiovascular events were not an outcome"], ["reduces heart attacks", "saves lives"]),
("S30", "linguistics", "corpus scope", "critique", "We examined one newspaper archive.",
 "Use of the new word rose sharply between 2015 and 2020 in this archive. Spoken language was not sampled.",
 "Counts were normalized per million words.", "Use of the new word rose sharply between 2015 and 2020 in this archive.",
 ["this archive", "Spoken language was not sampled"], ["the language changed", "everyone uses"]),
]


def build():
    items = []
    for (iid, domain, trap, mode, before, middle, after, selection, keys, forbidden) in R:
        passage = " ".join((before, middle, after))
        assert selection in middle, iid
        assert all(k in passage for k in keys), iid
        assert not any(f.lower() in passage.lower() for f in forbidden), iid  # 禁語不能本來就在原文
        items.append({"id": iid, "domain": domain, "trap": trap, "mode": mode, "paragraphs": [before, middle, after],
                      "selection": selection, "key_conditions": keys, "forbidden_claims": forbidden})
    assert len(items) == 30 and sum(i["mode"] == "explain" for i in items) == 15
    return {"schema": "roothinks.study.eval_dataset/1", "created": "2026-10-26",
            "provenance": "Synthetic passages written for this evaluation by the RootThinks cloud engineering agent. "
                          "Not taken from any paper, private document or sealed holdout.",
            "items": items}


if __name__ == "__main__":
    out = Path(__file__).with_name("dataset.json")
    text = json.dumps(build(), ensure_ascii=False, indent=1)
    out.write_text(text, encoding="utf-8")
    print(out, hashlib.sha256(text.encode("utf-8")).hexdigest())
