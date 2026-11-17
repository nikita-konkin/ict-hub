# Mapping Functions and IPP Geometry for GNSS Absolute TEC Estimation

Research notes for the AbsolTEC reconstruction. Scope: peer-reviewed literature on slant-to-vertical mapping functions and ionospheric pierce point (IPP) geometry for TEC. All facts below are tied to sources fetched or verified during this session (Nov 2026). Where a source could not be opened or verified, that is stated explicitly. Most important overall finding first:

**The recovered AbsolTEC model is a verbatim match to the published Yasyukevich-group algorithm.** The open-access successor paper of the same research group (Yasyukevich et al. 2020, *Sensors*) contains, as its Eq. (3) and Eq. (4), exactly the model reconstructed from the binary: I_M = S·[I_V + G_φ·Δφ + G_qφ·(Δφ)² + G_l·Δl + G_ql·(Δl)² + G_t·Δt + G_qt·(Δt)²] + I_DCB, with S = [cos(arcsin( (R_E/(R_E+h_max))·sin[α(90°−θ)] ))]⁻¹, R_E = 6371 km, h_max = 450 km, α = 0.97 (mid-lat) / 0.94 (high-lat) / 0.87 (low-lat). The official software page (gnss-lab.org TayAbsTEC, © 2017 Mylnikova & Yasyukevich) documents the same absolTEC.dia parameters (10° cutoff, 0.97 coefficient) and cites the 2015 papers as the algorithm description. The reconstruction's recovered choices are therefore *the published choices of the original authors*, not ad-hoc reverse-engineering artifacts — see each question below for how the literature judges those choices.

---

## Verify the two 2015 Yasyukevich papers: titles, authors, journal, mapping function, 0.97 coefficient, 450 km shell

### Takeaway
Both DOIs are real and verified. The Geomagnetism & Aeronomy paper (DOI 10.1134/S001679321506016X) is the primary description of the TayAbsTEC/AbsolTEC algorithm; the Results in Physics paper (DOI 10.1016/j.rinp.2014.12.006) is a 2-page short communication on the DCB estimation algorithm. The full text of the former is closed-access, so the exact mapping-function formula and the α = 0.97 coefficient could not be quoted directly from it; however, the identical algorithm with the identical formula, constants (6371 km, 450 km) and α values (0.97/0.94/0.87) is fully documented in the same group's open-access 2020 Sensors paper and on the official TayAbsTEC page.

### Cited Findings
- Bibliographic record of paper 1: Yu.V. Yasyukevich, A.A. Mylnikova, V.E. Kunitsyn, A.M. Padokhin, "Influence of GPS/GLONASS differential code biases on the determination accuracy of the absolute total electron content in the ionosphere", *Geomagnetism and Aeronomy*, 55(6), 763–769, 2015, DOI 10.1134/S001679321506016X (Pleiades/Springer; Russian original: Геомагнетизм и аэрономия). Verified via the official TayAbsTEC page, which lists it as the algorithm reference — [gnss-lab.org](http://www.gnss-lab.org/tay-abs-tec); record confirmed in OpenAlex (W2320192669, 114 citations, authors from ISTP SB RAS and Lomonosov MSU) — [openalex.org](https://openalex.org/works/W2320192669). Note: one PMC reference list gives the page range as "55:790–796" (Russian-original pagination vs. English-translation pagination); the canonical English version is 763–769 — [PMC7583837 reference list](https://pmc.ncbi.nlm.nih.gov/articles/PMC7583837).
- Bibliographic record of paper 2: Yu.V. Yasyukevich, A.A. Mylnikova, A.S. Polyakova, "Estimating the total electron content absolute value from the GPS/GLONASS data", *Results in Physics*, 5, 32–33, 2015, DOI 10.1016/j.rinp.2014.12.006 — verified by DOI resolution: [doi.org/10.1016/j.rinp.2014.12.006](https://doi.org/10.1016/j.rinp.2014.12.006). Its abstract (via ScienceDirect author page): a 1-ns DCB leads to a ~2.9 TECU error in L1–L2 dual-frequency oblique TEC; the paper presents a DCB-estimation algorithm from a single GPS/GLONASS station and compares it with CODE DCB data — [sciencedirect.com author page](https://www.sciencedirect.com/author/24075089800/yury-v-yasyukevich).
- Third related paper cited alongside (part of the same algorithm family): A.A. Mylnikova, Yu.V. Yasyukevich, V.E. Kunitsyn, A.M. Padokhin, "Variability of GPS/GLONASS differential code biases", *Results in Physics*, 5, 9–10, 2015, DOI 10.1016/j.rinp.2014.11.002 — [gnss-lab.org](http://www.gnss-lab.org/tay-abs-tec).
- The exact mapping function with the 0.97/0.94/0.87 coefficient and the 450 km shell, from the same group: Y. Yasyukevich, A. Mylnikova, A. Vesnin, "GNSS-Based Non-Negative Absolute Ionosphere Total Electron Content, its Spatial Gradients, Time Derivatives and Differential Code Biases: Bounded-Variable Least-Squares and Taylor Series" (TuRBOTEC), *Sensors*, 20(19), 5702, 2020, DOI 10.3390/s20195702. Eq. (4): S_ji = [cos{arcsin( (R_E/(R_E+h_max)) · sin[α(90−θ_ji)] )}]⁻¹, "where R_E = 6371 km is the Earth radius, h_max = 450 km is the ionospheric point height, and α is the correcting coefficient." Table 1 gives α = 0.97 (IRKJ, mid-lat), 0.87 (NTUS, equatorial), 0.94 (THU2, high-lat); the text adds α = 0.96 for THU2 under IRI-plas. The paper states the coefficient "adjusts a sharp non-physical TEC growth at low elevations, less than 30°" and that α "should be latitude-dependent" and disturbance-dependent — [PMC7583837](https://pmc.ncbi.nlm.nih.gov/articles/PMC7583837).
- The official TayAbsTEC page (the very software AbsolTEC reconstructs) documents absolTEC.dia: elevation cutoff 10°, and "0.97 the correcting coefficient: 0.97 – for mid-latitude stations, 0.94 – for high-latitude stations, 0.87 – for low-latitude stations"; it also documents the output columns (I_v; G_lon, G_lat; G_q_lon, G_q_lat; G_t, G_q_t; DCBs) matching the recovered model term-for-term — [gnss-lab.org](http://www.gnss-lab.org/tay-abs-tec).
- The 2020 paper explicitly attributes the α-modified mapping function to Schaer: "We use the modified single-layer model mapping function, and coefficient α is introduced to more correctly account for the ionospheric layer height [Schaer 1999]" — [PMC7583837](https://pmc.ncbi.nlm.nih.gov/articles/PMC7583837).
- The 2017 EGU abstract by the same authors confirms the algorithm name and structure: "The algorithm is based on TEC expansion into Taylor series in space and time (TayAbsTEC)" — Mylnikova, Yasyukevich & Yasyukevich, EGU2017-650-1 — [meetingorganizer.copernicus.org](https://meetingorganizer.copernicus.org/EGU2017/EGU2017-650-1.pdf).

### Inferences
- Because the 2020 Sensors paper (same lead authors, same institute, explicitly a successor of TayAbsTEC) contains the identical model equation, constants and α values that the reconstruction recovered, and the 2017 TayAbsTEC page documents the same 0.97 coefficient, the reconstructed choices (S = 1/√(1−sin²z′), sin z′ = R/(R+h)·sin[0.97·(90°−E)], h = 450 km, R = 6371 km) are certainly the intended published design, not artifacts of the 2017 binary.
- The coefficient 0.97 is the mid-latitude default; the original authors intended 0.94 and 0.87 for high- and low-latitude stations respectively, and the 2020 paper says even 0.96 is better at high latitudes under some conditions — so the reconstruction's "α as a fixed constant" should ideally be a station-latitude-dependent parameter.

### Gaps
- The full text of the 2015 *Geomagnetism and Aeronomy* paper (Springer, closed access) could not be fetched (link.springer.com returned an error page for this session); the mapping-function formula inside that specific paper was therefore not quoted directly. Its content is recoverable indirectly via the 2020 Sensors paper and the TayAbsTEC documentation, which describe the same algorithm.
- No source found that explains a possible latitude formula for choosing α beyond the three published values (0.97/0.94/0.87) and the IRI-plas adjustment (0.96).

---

## Standard single-layer model (SLM) and modified SLM (MSLM, Schaer 1999): definitions, shell heights, low-elevation error

### Takeaway
The SLM is the thin-shell model with mapping function F(z) = 1/cos z′, sin z′ = R/(R+H)·sin z; Schaer's MSLM multiplies the zenith angle by a correction coefficient α inside the sine (sin z′ = R/(R+H)·sin(αz)). Schaer's canonical MSLM fit (against the JPL extended slab model) uses H = 506.7 km and α = 0.9782 — numerically close to, but not identical with, the recovered 450 km + 0.97. Shell heights in the literature range 350–450 km (with 428.8 km the optimum approximating a Chapman-profile mapping function at <1% error); mapping-function error grows steeply below ~20–30° elevation.

### Cited Findings
- SLM definition and formula, with R0 = 6371 km and H = 400 km as the example: S. Schaer, "How to use CODE's Global Ionosphere Maps", Astronomical Institute, University of Berne, May 1997: E = F(z)·E_V = E_V/cos z′, sin z′ = (R/(R0+H))·sin z; "the height H of this idealized layer is usually set to the height of the expected maximum electron density: e.g. H = 400 km" — [abcdocz.com copy of the Schaer document](http://abcdocz.com/doc/176403/how-to-use-code%E2%80%99s-global-ionosphere-maps-stefan-schaer) and [gnss-x.ac.cn mirror](http://gnss-x.ac.cn/docs/gimman.pdf).
- MSLM formula with the α coefficient: "The modified SLM (MSLM) mapping function includes an additional constant, α (Schaer 1999): F_I(z) = 1/cos z′ with sin z′ = R/(R+H)·sin(αz)" — Yang, Li & Zhang, "Ionospheric correction for spaceborne single-frequency GPS based on single layer model", *Journal of Earth System Science*, 123(4), 767–778, 2014 — [ias.ac.in PDF](https://www.ias.ac.in/public/Volumes/jess/123/04/0767-0778.pdf).
- Schaer's canonical MSLM parameters: "The best fit with respect to the JPL extended slab model (ESM) mapping function is achieved at H = 506.7 km and coefficient of the zenith angle α = 0.9782 assuming a maximum zenith distance of 80° (Schaer, 2002)" — Gulyaeva, "Modeling Center-of-Mass of the Ionosphere From the Slab-Thickness", *Radio Science*, 56, e2020RS007209, 2021, DOI 10.1029/2020RS007209 — [Wiley page](https://agupubs.onlinelibrary.wiley.com/doi/10.1029/2020RS007209).
- Shell height of 450 km with base radius 6371 km was the IGS-recommended standard: IGS 1998 Darmstadt workshop report ("Ionospheric Products"), recommendation: "The height of the single layer should be 450 km adopting a base radius of 6371 km" — [gauss.gge.unb.ca IGS 1998 report](http://gauss.gge.unb.ca/IGS/final/ionopp98.pdf).
- 350 km is the classical choice: Klobuchar's original model assumes a thin layer at 350 km (Klobuchar, "Ionospheric Time-Delay Algorithms for Single-Frequency GPS Users", *IEEE Trans. Aerospace and Electronic Systems*, AES-23(3), 325–331, 1987) — reproduced by ESA Navipedia, [gssc.esa.int Navipedia Klobuchar](https://gssc.esa.int/navipedia/index.php/Klobuchar_Ionospheric_Model); and early GPS-TEC studies (Lanyi 1986; Coco et al. 1991; Wilson & Mannucci 1993) suggested 350 km, still used e.g. in Boston College GPS-TEC software — [ScienceDirect 2023 East Africa paper](https://www.sciencedirect.com/science/article/abs/pii/S0273117723001631).
- 428.8 km is the height that best approximates the Chapman-profile mapping function with <1% TEC mapping error (Schaer 1999): "the height of the assumed single layer was selected as H = 428.8 km, which is close to the optimal approximation of the Chapman profile mapping function that has less than 1% of TEC mapping error [Schaer, 1999]" — Liu et al., "Spherical cap harmonic analysis of the Arctic ionospheric TEC for one solar cycle", *JGR Space Physics*, 119, 2014, DOI 10.1002/2013JA019501 — [Wiley page](https://agupubs.onlinelibrary.wiley.com/doi/full/10.1002/2013JA019501).
- Height choice affects the mapping function mainly below 30°: MF at 250/350/450/500 km peaks at 3.67→2.67; "the difference of mapping functions at 30° elevation can be up to 0.13 [m per 1 m vertical delay]. … This variation will be as large as 0.68 m at 10° elevation" — Yang et al. 2014 — [ias.ac.in PDF](https://www.ias.ac.in/public/Volumes/jess/123/04/0767-0778.pdf).
- Fixed-height thin-shell errors: "Many researchers showed that the MF at a given fixed height has significant errors at the low elevation and the large ionosphere gradient with a fixed height (Birch et al. 2002; Conker and El-Arini 2002; Nava et al. 2007; Palamartchouk 2010; Wang et al. 2016; Zus et al. 2016)"; "Schaer (1999) … showed that there is a relative error of 10% with reference to the height of 350 km"; height "typically ranges from 300 km to 500 km (Hernández-Pajares et al. 2005), but for the convenience of the global ionospheric modeling, the height is usually fixed … at 450 km" — "Analysis Impacts of the Varying Heights on Ionospheric Modeling and DCB Estimation", ION GNSS+ 2017 — [ion.org abstract](http://www.ion.org/publications/abstract.cfm?articleID=15359).
- Birch et al. (2002) recommend effective heights of 600–1200 km because of the topside/plasmaspheric contribution; "a lower value could produce an error of 15%–30% in the electron content" — Gulyaeva 2021 (quoting Birch et al. 2002) — [Wiley page](https://agupubs.onlinelibrary.wiley.com/doi/10.1029/2020RS007209).
- Low-latitude effective height is highly variable: "the effective height of the ionosphere over the region is highly variable portraying diurnal and seasonal dependence … the suitable effective height of the ionosphere over low latitude region of East Africa ranges from 350 km to 600 km"; "the contribution of elevation angle of the satellite pass to the variation of mapping function value is negligible for elevation angle greater than 40°" — *Advances in Space Research*, 2023, "Characterization of the effective height of the ionosphere using GPS data over East Africa, a low latitude region" — [ScienceDirect](https://www.sciencedirect.com/science/article/abs/pii/S0273117723001631).
- A spherical-shell MF (with thickness) "can be overestimated up to 15% at the pierce point zenith angle of 70° and the height of 200 km (Smith et al. 2008)" — ION GNSS+ 2017 — [ion.org](http://www.ion.org/publications/abstract.cfm?articleID=15359).
- GLONASS IAC's operational description uses the same 1/sin(E′) form with h = 450 km, R⊕ = 6371 km: "STEC = VTEC·m(E) = VTEC·1/sin(E′), cos(E′) = R⊕/(R⊕+h)·cos(E)" — [glonass-iac.ru ionosphere methods](https://glonass-iac.ru/en/iono/methods).

### Inferences
- The recovered S = 1/√(1−sin²z′) with sin z′ = R/(R+h)·sin(α·(90°−E)) is algebraically identical to both the SLM formula (α = 1) and Schaer's MSLM (α in the sine argument), matching the published Yasyukevich form exactly. The 0.97 coefficient is therefore a legitimate, literature-documented MSLM variant, not an anomaly.
- The recovered 450 km shell matches the IGS-recommended height and the height used in the TuRBOTEC paper, but the literature indicates it is a compromise: 428.8 km minimizes thin-shell error against a Chapman profile, 506.7 km is Schaer's MSLM optimum, and the true "effective height" varies 350–600 km (or more, if topside content is included).
- Low-elevation data below ~20–30° are where the fixed-shell MF error concentrates; with the 10° cutoff used by TayAbsTEC, the MF error at 10–20° elevation is a first-order accuracy limit (Yang et al. 2014 quantify 0.68 m per 1 m vertical delay difference between shell heights at 10°).

### Gaps
- Schaer's 1999 thesis itself (Schweizerische Geodätische Kommission / Astronomical Institute University of Berne) was not fetched in full; MSLM numbers are cited via secondary sources (Gulyaeva 2021; Yang et al. 2014; Liu et al. 2014; ION 2017) which all agree.
- The original (Schaer 1999) derivation of α = 0.9782 could not be quoted verbatim.

---

## Elevation-dependent mapping errors at low elevations and elevation cutoffs used in practice

### Takeaway
Thin-shell mapping error grows steeply below ~20–30° elevation, and 10–20° are the cutoffs used by the major IGS analysis centers and by the original TayAbsTEC itself (10°); data below ~10° are generally excluded from TEC mapping to avoid gross mapping errors, though the exact error magnitude in TECU depends on TEC level and location.

### Cited Findings
- IGS analysis-center practice at the 1998 Darmstadt workshop: elevation cutoffs of 10°, 15° and 20° across centers (CODE 20°; JPL 10°; ESOC 10°; UPC 20°; etc.), all with the 1/cos z′ SLM mapping function — [IGS 1998 report table](http://gauss.gge.unb.ca/IGS/final/ionopp98.pdf).
- CODE's 20° cutoff implies a probed area "about 2 000 kilometers" in diameter per station — Schaer 1997 — [abcdocz.com](http://abcdocz.com/doc/176403/how-to-use-code%E2%80%99s-global-ionosphere-maps-stefan-schaer).
- TayAbsTEC/AbsolTEC uses 10°: absolTEC.dia "10 elevation angle cutoff" — [gnss-lab.org](http://www.gnss-lab.org/tay-abs-tec); the 2020 algorithm paper likewise: "we use the data with elevations greater than 10°" — [PMC7583837](https://pmc.ncbi.nlm.nih.gov/articles/PMC7583837).
- Klobuchar's original 1987 document considered 5° as the lowest elevation handled; at 5° elevation the IPP is "approximately 14 degrees of earth angle away from the user location" (for the 350 km shell) — [ION museum copy of Klobuchar's algorithm document](http://www.ion.org/museum/upload/47-klobuchars-algorithm-for-original-document.pdf).
- Mapping-function differences between different shell heights are negligible above 40° elevation but significant below 30°: up to 0.13 m per 1 m vertical delay at 30° and 0.68 m at 10° — Yang et al. 2014 — [ias.ac.in PDF](https://www.ias.ac.in/public/Volumes/jess/123/04/0767-0778.pdf).
- The rationale of the α coefficient is precisely to damp the unphysical MF growth at low elevations: "By its physical implication, the coefficient α adjusts a sharp non-physical TEC growth at low elevations, less than 30°" — Yasyukevich et al. 2020 — [PMC7583837](https://pmc.ncbi.nlm.nih.gov/articles/PMC7583837).
- Topside plasma makes low-elevation mapping worse than the thin shell assumes, especially at night: "the contributions of topside plasma to the mapping function are also substantial, especially at night when the electron density decreases compared to the topside plasma (Birch et al. 2002)" — ION GNSS+ 2017 — [ion.org](http://www.ion.org/publications/abstract.cfm?articleID=15359).
- In the TuRBOTEC weighting scheme, high-elevation measurements dominate (weight ∝ 1/S), consistent with distrust of low-elevation mapping — Eq. (6) of [PMC7583837](https://pmc.ncbi.nlm.nih.gov/articles/PMC7583837).

### Inferences
- The 10° cutoff recovered in the AbsolTEC configuration file is the original authors' documented choice and lies within mainstream IGS practice (10–20°); it is consistent with the literature's warning that MF errors dominate below ~20°.
- For a reconstruction aiming at scientific fidelity, keeping the 10° cutoff is defensible; tightening to 15–20° would reduce MF error but shrink the IPP spatial spread (and hence the observability of the G_lon/G_lat gradient terms), so it is a trade-off rather than a clear improvement.

### Gaps
- No source quantified the thin-shell MF error in TECU specifically at 10° vs 20° elevation as a single universal number (it depends on VTEC level and location); the available quantifications are in relative terms (10% vs 350-km reference — Schaer 1999; 15% shell overestimate — Smith et al. 2008; 15–30% for too-low heights — Birch et al. 2002; 0.68 m per 1 m vertical delay at 10° — Yang et al. 2014).

---

## Mapping functions with a correction coefficient inside the sine argument (the recovered 0.97)

### Takeaway
Yes — this is Schaer's modified single-layer model (MSLM), introduced in his 1999 thesis/2002 publications: sin z′ = R/(R+H)·sin(αz). Schaer's canonical constants are α = 0.9782 with H = 506.7 km (fit to the JPL extended slab model up to z = 80°); the Yasyukevich group's variant keeps H = 450 km and makes α latitude-dependent (0.97 mid-latitude, 0.94 high-latitude, 0.87 low-latitude), tuned against IRI-2012.

### Cited Findings
- MSLM definition with α inside the sine: "The modified SLM (MSLM) mapping function includes an additional constant, α (Schaer 1999): F_I(z) = 1/cos z′ with sin z′ = R/(R+H)·sin(αz)" — Yang et al. 2014, *J. Earth Syst. Sci.* 123(4) — [ias.ac.in PDF](https://www.ias.ac.in/public/Volumes/jess/123/04/0767-0778.pdf).
- Schaer's α value: "the best fit with respect to the JPL extended slab model (ESM) mapping function is achieved at H = 506.7 km and coefficient of the zenith angle α = 0.9782 assuming a maximum zenith distance of 80° (Schaer, 2002)" — Gulyaeva 2021, *Radio Science* — [Wiley](https://agupubs.onlinelibrary.wiley.com/doi/10.1029/2020RS007209).
- The exact recovered formula, verbatim, with α = 0.97: "S_ji = [cos{arcsin( (R_E/(R_E+h_max))·sin[α(90−θ_ji)] )}]⁻¹, where R_E = 6371 km … h_max = 450 km … and α is the correcting coefficient" and "We use the modified single-layer model mapping function, and coefficient α is introduced to more correctly account for the ionospheric layer height [34 = Schaer 1999]" — Yasyukevich et al. 2020, *Sensors* — [PMC7583837](https://pmc.ncbi.nlm.nih.gov/articles/PMC7583837).
- Latitude-dependent α values: Table 1 (α: IRKJ 0.97, NTUS 0.87, THU2 0.94) and the statement that "due to the ionosphere peculiarities, the coefficient α should be latitude-dependent. Also, the α parameter should affect the ionosphere disturbance level…" — [PMC7583837](https://pmc.ncbi.nlm.nih.gov/articles/PMC7583837).
- Figure 4 of the same paper shows the α effect on the slant-to-vertical conversion vs. IRI-2012 slant TEC, with α = 1 (unmodified SLM) overestimating at low elevations and α = 0.97/0.94/0.87 suppressing the unphysical growth below 30° — [PMC7583837](https://pmc.ncbi.nlm.nih.gov/articles/PMC7583837).
- The software documentation of the original program states the same: "0.97 the correcting coefficient: 0.97 – for mid-latitude stations, 0.94 – for high-latitude stations, 0.87 – for low-latitude stations" — [gnss-lab.org](http://www.gnss-lab.org/tay-abs-tec).
- Klobuchar's broadcast model uses a different (not sine-argument) obliquity factor F = 1 + 16·(0.53 − E)³ — for contrast: [ESA Navipedia](https://gssc.esa.int/navipedia/index.php/Klobuchar_Ionospheric_Model).

### Inferences
- The 0.97 coefficient is scientifically sound and published: it is Schaer's MSLM concept with the Yasyukevich group's constants. The reconstruction should not "fix" or remove it; if anything, the literature supports making it configurable by station geomagnetic latitude (0.97/0.94/0.87).
- Strictly, Schaer's own MSLM constant pair is (506.7 km, 0.9782), so the recovered pair (450 km, 0.97) is the Yasyukevich variant, whose published rationale is a fit to IRI-2012 slant TEC. Both are defensible; a reviewer comparing against the Bernese/CODE convention would note the different H.

### Gaps
- The specific fitting procedure by which 0.97/0.94/0.87 were derived (beyond "simulation used the IRI-2012 model", Figure 4) is not detailed in the available open text.
- Whether the 2015 Geomagnetism & Aeronomy paper itself introduced the 0.97 value (rather than the 2020 Sensors paper documenting an earlier choice) could not be confirmed from full text (closed access); the 2017 TayAbsTEC page already documents 0.97, so it predates 2020.

---

## IRI-based and voxel/geometric mapping functions as alternatives to the thin shell

### Takeaway
More accurate alternatives exist and are well quantified: two-layer tomographic MFs (UPC TOMION, 450 + 1130 km), the Barcelona Ionospheric Mapping Function (BIMF, a climatological two-layer MF needing no external data, improving GIM STEC by up to 15–32%), Chapman-profile and IRI-based MFs (Hoque & Jakowski; Zus et al. Potsdam MF), and variable-shell-height approaches (IRI-2016-derived heights; Gulyaeva's center-of-mass height). A NeQuick-based MF was found by the Yasyukevich group not to improve accuracy materially.

### Cited Findings
- BIMF: H. Lyu, M. Hernández-Pajares, M. Nohutcu, A. García-Rigo, H. Zhang, J. Liu, "The Barcelona ionospheric mapping function (BIMF) and its application to northern mid-latitudes", *GPS Solutions*, 22, 67, 2018, DOI 10.1007/s10291-018-0731-0: based on the climatological topside VTEC fraction μ2; "the use of BIMF improves the GIM STEC estimation compared to the single-layer assumptions … up to 15% for the year 2014 [UPC GIMs] … CODE and JPL, up to 32 and 29%, respectively" — [Springer PDF](https://link.springer.com/content/pdf/10.1007/s10291-018-0731-0.pdf).
- BIMF mechanics: two layers with effective heights 450 km and 1130 km (UPC TOMION central values), μ2 as the topside shape function "in terms of a low-degree polynomial function on local time with coefficients previously fitted on few significant terms of Fourier series on time with data from 1998.4 to 2009.4" — [IGS Workshop 2018 POLarGIMs presentation](https://files.igs.org/pub/resource/pubs/workshop/2018/IGSWS-2018-PS11-02.pdf).
- UPC's two-layer/tomographic model is the reference: Hernández-Pajares et al. 1999 (TOMION) — cited throughout [BIMF paper](https://link.springer.com/content/pdf/10.1007/s10291-018-0731-0.pdf).
- Chapman-profile / IRI-based MFs: the BIMF paper contrasts with "approaches based on the Chapman layer assumption (Hoque and Jakowski 2013) or on electron density fields from climatic models like IRI (Zus et al. 2016)" — [BIMF paper](https://link.springer.com/content/pdf/10.1007/s10291-018-0731-0.pdf); the Potsdam MF "is proposed to consider the location, elevation, azimuth, and even bending effects (Zus et al. 2016)" — [ION GNSS+ 2017](http://www.ion.org/publications/abstract.cfm?articleID=15359).
- IRI-2016 variable-height MF: "a mapping function with variable heights from the latest IRI 2016 will be developed, aiming to reduce the mapping errors for the ionospheric modeling" — [ION GNSS+ 2017](http://www.ion.org/publications/abstract.cfm?articleID=15359).
- Center-of-mass height as a varying shell height: Gulyaeva 2021 proposes substituting Hc (ionospheric center-of-mass height from slab thickness) for the fixed shell height — [Wiley](https://agupubs.onlinelibrary.wiley.com/doi/10.1029/2020RS007209).
- NeQuick-based MF verdict from the AbsolTEC authors themselves: "Schüler and Oladipo suggested one use the NeQuick model instead of the thin layer approximation to convert the slant TEC into the vertical one. However, such an approach did not provide an essential accuracy increase" — [PMC7583837](https://pmc.ncbi.nlm.nih.gov/articles/PMC7583837).
- NeQuick-based MF with horizontal gradients: Zernov, Danilogorskaya, Gherm, Radicella, Nava, "The New Technique for Calculating the Ionospheric Phase Advance and the Mapping Function for TEC Built on the Basis of NeQuick Model of the Ionosphere", Beacon Satellite Symposium 2016 — [sites.bc.edu PDF](https://sites.bc.edu/bss2016/wp-content/uploads/sites/203/2023/11/Zernov_HorGrad_TEC.pdf).
- Azimuth-dependent MF (2022): "Modeling and analysis of an ionospheric mapping function considering azimuth angle: A preliminary result", *Advances in Space Research*, 2022 (SGG-MF): MAE of SLM-MF/Klobuchar-MF/MSLM-MF 1.5–2.1 TECU vs 1.5–1.7 TECU for SGG-MF; STD improved ~13.6–14.9%; SF-PPP error reduced 6.0–22.9% — [ScienceDirect](https://sciencedirect.com/science/article/pii/S0273117722006640).
- JPL GIMs are based on a multi-layer model (contrasted with CODE's single-layer MSLM) — noted in [PMC7583837](https://pmc.ncbi.nlm.nih.gov/articles/PMC7583837).

### Inferences
- For a single-station, single-epoch absolute-TEC tool, the published evidence supports the thin-shell/MSLM choice as reasonable (the original authors tested a NeQuick alternative and found no gain), but a BIMF-style or IRI-variable-height upgrade is the literature's documented path to materially better low-elevation mapping.
- Any improved reconstruction that keeps the same least-squares structure could substitute S with a BIMF (41 coefficients, no external data) without changing the model equation form.

### Gaps
- No head-to-head study was found comparing the Yasyukevich α-MSLM specifically against BIMF/Chapman MFs for single-station absolute TEC; the comparisons found are for GIM STEC conversion and SF-PPP.

---

## Second- and third-order ionospheric corrections: magnitude in TECU

### Takeaway
The second-order (magnetionic) term is a small fraction of first-order TEC — at 100 TECU it is ~0.05–0.1 TECU-equivalent at L1 (≈0.8 cm carrier, ≈0.6 cm dual-frequency residual per Bassiri & Hajj 1993), reaching ~0.16% of the first-order term at L1 under extreme conditions (Datta-Barua et al. 2008). It matters for mm–cm positioning, not for TEC mapping at the 0.5–1 TECU level. Third-order is ~1–2 orders smaller still. For a TEC-mapping program at 10° cutoff, second/third-order terms are negligible for TEC output but worth documenting.

### Cited Findings
- Bassiri & Hajj 1993 (the origin of the thin-layer second-order assumption): "in the case of 100 TECU, the second-order ionospheric errors on L1 and L2 carrier phases are about −0.8 cm and −1.7 cm, respectively, and the second-order RIE after the dual-frequency combination is about 0.6 cm" (as summarized by the JASTP 2015 paper); they chose a 300 km single-layer height for the B-field evaluation — [ScienceDirect JASTP 2015 review](https://www.sciencedirect.com/science/article/abs/pii/S1364682615001157) and [Hoque & Jakowski 2008 Radio Science](https://agupubs.onlinelibrary.wiley.com/doi/10.1029/2007RS003817).
- Kedar et al. 2003: accounting for the second-order term shifts station positions southward by 0.1–0.5 cm; used a 400 km layer — [JASTP 2015](https://www.sciencedirect.com/science/article/abs/pii/S1364682615001157); [Hoque & Jakowski 2008](https://agupubs.onlinelibrary.wiley.com/doi/10.1029/2007RS003817).
- Hernández-Pajares et al. 2007 ("Second-order ionospheric term in GPS: Implementation and impact on geodetic estimates", *JGR*, 112, B08417, DOI 10.1029/2006JB004707): uses a 450 km single layer; using IGRF instead of a dipolar field reduces second-order errors by up to 60% — as summarized in [arXiv:0908.1905](https://arxiv.org/pdf/0908.1905); the second-order term causes a southward translation of the reference frame of 3–10 mm (Petrie et al. 2011; Garcia-Fernandez et al. 2012) — [AGU 2012 poster](https://acc.igs.org/iono/agu12_garcia_iono_2nd_order_cmp.pdf).
- Orders of magnitude per 100 TECU: on L1–L2 combination, I1 ≈ 30 ns delay per 100 TECU, I2 ≈ 0–130 ps, I3 ≈ 0–3 ps (after Hernández-Pajares et al. 2008) — [arXiv:0908.1905 table](https://arxiv.org/pdf/0908.1905). In TECU terms (1 TECU = 0.162 m at L1), the L1 second-order delay of 0.8 cm per 100 TECU ≈ 0.05 TECU.
- Upper bounds at low elevation/extreme conditions: "remaining phase errors to reach up to 8 cm and group errors up to 20 cm at low elevation on days of extreme ionospheric activity. These values are 0.06% and 0.16% of the first order error at L1 frequency, respectively (Datta-Barua et al., 2008)" — [Hoque & Jakowski 2010 ASR](https://www.sciencedirect.com/science/article/abs/pii/S0273117710001183).
- Second-order range error vs azimuth/elevation: up to 60 mm on L2 for VTEC = 100 TECU at 10° elevation at a mid-European station (Jakowski et al., "Ionospheric Impact on GNSS Signals", *Física de la Tierra* 20, 11–25, 2008) — [revistas.ucm.es PDF](https://revistas.ucm.es/index.php/FITE/article/download/FITE0808110011A/11458).
- Third-order: "the dual-frequency third-order residual error can be as big as about 3 mm during high TEC values and low elevation angles such as TEC_V = 143 TECU and 1° elevation. The proposed third-order correction formula can correct this error within the submillimeter level" — Hoque & Jakowski 2008, *Radio Science*, 43, RS4018, DOI 10.1029/2007RS003817 — [Wiley](https://agupubs.onlinelibrary.wiley.com/doi/10.1029/2007RS003817).
- IPP-B-field approximation error for second-order corrections: 0–2 mm — [Hoque & Jakowski 2008](https://agupubs.onlinelibrary.wiley.com/doi/10.1029/2007RS003817).
- In radio occultation (large STEC), second-order effects reach cm in range and >1 TECU in TEC: for VTEC 216/167 TECU, max second-order RIEs ≈ 5 cm / 4.5 cm; L1/L2 ray separation exceeds 1 km for VTEC > 160 TECU, with TEC errors exceeding 10 TECU in occultation geometry — Hoque & Jakowski 2010, *ASR* — [ScienceDirect](https://www.sciencedirect.com/science/article/abs/pii/S0273117710001183).

### Inferences
- For AbsolTEC's domain (ground-based, 10° cutoff, TEC accuracy target of order 0.5–1 TECU), second-order corrections are below the noise floor of pseudorange-derived absolute TEC (pseudorange noise of several TECU); their omission is scientifically standard for TEC mapping and does not need to be "fixed", but a note in any reproduction paper is warranted.
- If the reconstruction is ever used to emulate precision positioning or RO products, second-order corrections (with IGRF at the IPP, 450 km layer) become necessary.

### Gaps
- No source was found that states the second-order term directly in TECU for typical mid-latitude STEC; the TECU-equivalent values given in the inferences are conversions from the cited cm/TECU numbers (1 TECU = 0.162 m at L1).

---

## IPP longitude/latitude computation: standard spherical formulas, quadrant, polar and dateline handling

### Takeaway
The classical literature (Klobuchar 1987; DO-229/NTCM-G descendants) computes the IPP by the earth-central angle ψ = π/2 − E − arcsin[(R/(R+h))·cos E], then φ_ipp = arcsin(sinφu·cosψ + cosφu·sinψ·cosA), and λ_ipp = λu + arcsin[sinψ·sinA / cos φ_ipp] — i.e., an asin-based longitude, which is exactly the construct the recovered binary uses. That asin form is quadrant-ambiguous (fails to distinguish the eastern/western branch for |A| > 90°) and singular near the poles (cos φ_ipp → 0); Klobuchar handled polar cases by clamping the IPP latitude to ±74.88° (±0.416 semicircles) and, above 75° user latitude, by using the observer's latitude directly.

### Cited Findings
- Klobuchar's original spherical formulas (his Eq. 5–6, ION museum scan): φ_i = sin⁻¹{sinφ0 cosψ + cosφ0 sinψ cosA}; λ_i = λ0 + sin⁻¹{sinψ sinA / cos φ_i}; flat-earth approximations φ_i = φ0 + ψ cosA, λ_i = λ0 + ψ sinA/cos φ0 (Eq. 7–8); error statements: flat-earth latitude/longitude errors max ~1° at 40° latitude, ~3°/2° at 60° latitude and 5° elevation; "At high latitudes above 75 degrees an ionospheric latitude equal to the observer location is recommended, regardless of observer's elevation angle" — Klobuchar 1987 algorithm document — [ion.org museum PDF](http://www.ion.org/museum/upload/47-klobuchars-algorithm-for-original-document.pdf).
- The broadcast-model IPP procedure (IS-GPS-200 style, with latitude clamp): ψ = 0.0137/(E+0.11) − 0.022 (semicircles); φ_I = φ_u + ψ cosA; clamp φ_I to ±0.416 semicircles (= ±74.88°); λ_I = λ_u + ψ sinA / cos φ_I; then geomagnetic latitude φ_m = φ_I + 0.064 cos(λ_I − 1.617) — [ESA Navipedia Klobuchar](https://gssc.esa.int/navipedia/index.php/Klobuchar_Ionospheric_Model).
- The general spherical-shell IPP equations used by SBAS (RTCA DO-229-derived, NTCM-G documentation): ψ_pp = π/2 − E − arcsin( R_e/(R_e+h_I)·cos E ); φ_pp = arcsin( sinφu·cosψ_pp + cosφu·sinψ_pp·cosA ); λ_pp = λu + arcsin( sinψ_pp·sinA / cos φ_pp ) — [NTCM-G Ionospheric Model Description v1.0](https://www.gsc-europa.eu/sites/default/files/NTCM-G_Ionospheric_Model_Description_-_v1.0.pdf).
- Note the NTCM-G/DO-229 longitude equation is literally the asin form, i.e., the form implemented in the recovered binary ("asin-based IPP longitude") is a documented standard formulation, not an author-specific invention — [NTCM-G document, Eq. 26](https://www.gsc-europa.eu/sites/default/files/NTCM-G_Ionospheric_Model_Description_-_v1.0.pdf).
- The equivalent single-layer geometry is also used with 450 km: "cos(E′) = R⊕/(R⊕+h)·cos(E)" with h = 450 km — [glonass-iac.ru](https://glonass-iac.ru/en/iono/methods).

### Inferences
- The recovered asin longitude reproduces Klobuchar's (1987) and DO-229's published formulas, so it is historically faithful; however the quadrant loss is real: asin maps westward-looking rays (sinA < 0) to negative Δλ correctly, but for azimuths in the western half-plane the asin result equals −(the true eastward displacement magnitude) which coincides with the correct Δλ only when the spherical triangle's Δλ magnitude is ≤ 90°; near the poles (cos φ_ipp ≈ 0) the formula is numerically unstable, exactly the failure mode noted in the reconstruction brief. The quadrant-safe standard used by modern geodetic software replaces the longitude step with a two-argument form (Δλ = atan2(sinψ·sinA·cosφu, cosψ − sinφu·sinφ_ipp)) — this is engineering practice (e.g., common in RTKLIB-derived code); no dedicated peer-reviewed paper discussing the quadrant fix was found, so treat this as implementation guidance, not a literature claim.
- Dateline wrapping: none of the cited sources discuss explicit normalization of λ_ipp into [−180°, 180°) or [0°, 360°); Klobuchar's document specifies that user longitudes are "in the range [0, 2π]", i.e., 0–360° convention. Wrap-around handling is left to implementers.

### Gaps
- No peer-reviewed paper was found that explicitly analyzes the asin-vs-atan2 IPP-longitude quadrant problem or polar singularity; the sources above document the asin form (and Klobuchar's ad-hoc polar clamps) without flagging the ambiguity. The atan2 recommendation above is an inference from spherical geometry, clearly labeled.
- No source ties the recovered binary constant 6371302.0 m to any publication. All literature sources use R = 6371 km exactly (Schaer 1997; IGS 1998 recommendation "base radius of 6371 km"; TuRBOTEC R_E = 6371 km; Yang et al. 2014). 6371302 m = 6371.302 km; no cited work uses this value, and its origin in the binary (possibly a rounded mean-Earth radius such as 6371.3 km) remains unexplained.

---

## Recent (2020–2026) mapping-function studies

### Takeaway
The newest dedicated mapping-function works found are from 2021–2025: Gulyaeva 2021 (center-of-mass height as a variable shell height), the 2022 azimuth-dependent SGG-MF (Adv. Space Res.), the 2023 East-Africa effective-height study (350–600 km), and a 2025 Adv. Space Res. assessment of SLM/MSLM/Klobuchar mapping functions in SF-PPP across solar activities. No 2025–2026 paper was found that supersedes the thin-shell/MSLM paradigm for single-station absolute TEC.

### Cited Findings
- 2021: Gulyaeva, *Radio Science* 56, e2020RS007209 (DOI 10.1029/2020RS007209) — proposes the ionospheric center-of-mass height Hc as a variable shell height replacing fixed H in the modified SLM function; quotes the Schaer MSLM constants (506.7 km, α = 0.9782) and the Birch et al. (2002) 600–1200 km recommendation — [Wiley](https://agupubs.onlinelibrary.wiley.com/doi/10.1029/2020RS007209).
- 2022: "Modeling and analysis of an ionospheric mapping function considering azimuth angle: A preliminary result" (*Advances in Space Research*, published online Nov 2022, article S0273117722006640): azimuth-aware SGG-MF; MAE 1.5–2.1 TECU for SLM/Klobuchar/MSLM vs 1.5–1.7 TECU for SGG-MF in coinciding-pierce-point tests; SF-PPP error reduced 6.0% (vs SLM) to 22.9% (vs Klobuchar) — [ScienceDirect](https://sciencedirect.com/science/article/pii/S0273117722006640).
- 2023: "Characterization of the effective height of the ionosphere using GPS data over East Africa, a low latitude region" (*Advances in Space Research*, article S0273117723001631): effective height 350–600 km with diurnal/seasonal variation; MF contribution negligible above 40° elevation — [ScienceDirect](https://www.sciencedirect.com/science/article/abs/pii/S0273117723001631).
- 2025: "Assessment of selected ionospheric mapping functions using SF-PPP on different solar activities" (*Advances in Space Research*, article S0273117724008901, available online Jan 2025): evaluates SLM MF, MSLM MF and Klobuchar MF in SF-PPP under different solar activity — [ScienceDirect](https://www.sciencedirect.com/science/article/pii/S0273117724008901).
- 2020: Hernández-Pajares et al., "Polar Electron Content From GPS Data-Based Global Ionospheric Maps…", *JGR Space Physics*, 125, e2019JA027677 (DOI 10.1029/2019JA027677) — polar extension of the BIMF concept discussed (POLarGIMs work) — [Wiley](https://agupubs.onlinelibrary.wiley.com/doi/full/10.1029/2019JA027677).
- Newest paper encountered in any search: Li et al., "Performance of GNSS dual-frequency combinations in estimating TEC based on ROTI", *GPS Solutions*, 30, 25 (2026), DOI 10.1007/s10291-025-01987-6, published 15 Nov 2025 — not a mapping-function study, but confirms the field's continued activity through late 2025 — [Springer](https://link.springer.com/article/10.1007/s10291-025-01987-6).

### Inferences
- The recovered MSLM-style mapping function with α ≈ 0.97 remains within the family of functions still being assessed and used in 2025 publications; nothing found in 2020–2026 renders it obsolete for single-station absolute TEC, though the recurring theme of the recent literature is that fixed-height isotropic MFs are the weakest at low elevation and that variable-height/azimuth/topside-aware forms improve STEC conversion by ~10–30% in specific metrics.

### Gaps
- The 2025 ASR SF-PPP mapping-function assessment was only available as an abstract; its quantitative ranking of SLM/MSLM/Klobuchar could not be extracted.
- No 2026 (current year) mapping-function-specific publication was found; the newest confirmed items are late-2025.

---

## Summary judgment for the reconstruction (cross-cutting)

### Takeaway
Every recovered choice is verifiably the published design of the original authors: the model equation, the 450 km shell, the 6371 km radius, the α = 0.97 coefficient, the 10° elevation cutoff, and the asin-based IPP longitude all appear verbatim in the Yasyukevich group's publications and software documentation; the α-coefficient concept itself is Schaer's MSLM. The literature both supports the choices (450 km is the IGS standard; 0.97 ≈ Schaer's 0.9782; 10° cutoff is mainstream) and identifies their known weaknesses (fixed-height MF error below 20–30° elevation; α should vary with latitude and disturbance level; asin longitude loses the quadrant near the poles and for certain azimuths).

### Cited Findings
- Model equation match: TuRBOTEC Eq. (3) is the recovered equation term-by-term (V, linear and quadratic lat/lon gradients, linear and quadratic time derivatives, satellite DCB) — [PMC7583837](https://pmc.ncbi.nlm.nih.gov/articles/PMC7583837).
- Constants match: R_E = 6371 km, h_max = 450 km, α = 0.97 mid-latitude — [PMC7583837](https://pmc.ncbi.nlm.nih.gov/articles/PMC7583837); cutoff 10° and 0.97 coefficient — [gnss-lab.org](http://www.gnss-lab.org/tay-abs-tec).
- 450 km/6371 km are the IGS-recommended standard pair — [IGS 1998 report](http://gauss.gge.unb.ca/IGS/final/ionopp98.pdf).
- α inside the sine argument is Schaer's MSLM (α = 0.9782, H = 506.7 km canonical) — [Gulyaeva 2021](https://agupubs.onlinelibrary.wiley.com/doi/10.1029/2020RS007209); [Yang et al. 2014](https://www.ias.ac.in/public/Volumes/jess/123/04/0767-0778.pdf).
- Fixed-height MF error concentrates below ~20–30° elevation — [ION GNSS+ 2017](http://www.ion.org/publications/abstract.cfm?articleID=15359); [Yang et al. 2014](https://www.ias.ac.in/public/Volumes/jess/123/04/0767-0778.pdf); [East Africa 2023](https://www.sciencedirect.com/science/article/abs/pii/S0273117723001631).
- asin longitude form is the published standard form (Klobuchar 1987; DO-229/NTCM-G) with documented polar workarounds — [Klobuchar 1987 scan](http://www.ion.org/museum/upload/47-klobuchars-algorithm-for-original-document.pdf); [NTCM-G description](https://www.gsc-europa.eu/sites/default/files/NTCM-G_Ionospheric_Model_Description_-_v1.0.pdf).

### Inferences
- Priority improvement candidates, ranked by literature support: (1) make α latitude-configurable (0.97/0.94/0.87) rather than fixed — the original authors' own recommendation; (2) optionally upgrade S to a variable-height or BIMF-style mapping function if low-elevation (10–20°) data quality matters; (3) replace the asin IPP longitude with the quadrant-safe atan2 form (keeping Klobuchar-compatible latitude clamping for polar stations); (4) document, but do not "fix", the 6371302.0 m constant, since all published sources use 6371 km exactly and the discrepancy (~300 m) has no literature explanation.

### Gaps
- Full text of Schaer (1999) thesis and of the 2015 Geomagnetism & Aeronomy paper were not accessible in this session; both are covered indirectly by agreeing secondary sources.
- No literature explanation of the binary's 6371302.0 m literal was found.
