export interface ResearchPaper {
    id: string;
    title: string;
    authors: string;
    year: number;
    journal: string;
    url: string;
    doi: string;
    category: 'fundamentals' | 'stress-detection' | 'hardware' | 'predictive-analytics';
    categoryLabel: string;
    greenmindLink: string;
    abstract: string;
}

export const RESEARCH_PAPERS: ResearchPaper[] = [
    // ─── Peter Gloor Papers ───────────────────────────────────
    {
        id: 'gloor-biosensors-2025',
        title: 'Plant Bioelectrical Signals for Environmental and Emotional State Classification',
        authors: 'Gloor PA, Kruse L, Oezkaya B',
        year: 2025,
        journal: 'Biosensors (MDPI)',
        url: 'https://doi.org/10.3390/bios15110744',
        doi: '10.3390/bios15110744',
        category: 'predictive-analytics',
        categoryLabel: 'Prädiktive Analytik',
        greenmindLink: 'Peter Gloor zeigt, dass eine Purple-Heart-Pflanze via bioelektrischer Signale Umgebungszustände (85,4 % Genauigkeit) und menschliche Emotionen (73 %) klassifizieren kann. Dies belegt, dass Pflanzensignale — wie GreenMind sie erfasst — mit ML hochpräzis auswertbar sind.',
        abstract: 'Studie zur Klassifikation von Umgebungs- und Emotionszuständen mittels bioelektrischer Signale einer Tradescantia pallida, erfasst mit AD8232-Sensor und ausgewertet mit CNN-Modellen.',
    },
    {
        id: 'gloor-basil-stress-2026',
        title: 'Predicting Human Stress and Exam Performance through Variations of the Electrical Potentials of a Basil Plant',
        authors: 'Gloor PA, Fronzetti Colladon A, Nann S',
        year: 2026,
        journal: 'Book Chapter (forthcoming)',
        url: 'https://www.researchgate.net/publication/388062755',
        doi: 'forthcoming',
        category: 'stress-detection',
        categoryLabel: 'Stress-Detektion',
        greenmindLink: 'Peter Gloor misst Veränderungen der Aktionspotenziale einer Basilikum-Pflanze, um menschlichen Stress vorherzusagen — mit über 90 % Genauigkeit. Dies zeigt, dass bioelektrische Pflanzensignale ein hochsensitives Messinstrument für Umgebungsveränderungen sind.',
        abstract: 'Laborexperiment mit 30 Teilnehmenden: Ein Plant SpikerBox erfasst die Aktionspotenziale einer Basilikum-Pflanze, ML-Modelle sagen Prüfungsnoten mit >90 % Genauigkeit voraus.',
    },
    {
        id: 'gloor-5year-arxiv-2025',
        title: 'Plant Bioelectric Early Warning Systems: A Five-Year Investigation into Human-Plant Electromagnetic Communication',
        authors: 'Gloor PA, Oezkaya B, Kruse L, et al.',
        year: 2025,
        journal: 'arXiv Preprint',
        url: 'https://arxiv.org/abs/2506.06968',
        doi: 'arXiv:2506.06968',
        category: 'fundamentals',
        categoryLabel: 'Grundlagen',
        greenmindLink: 'Diese 5-Jahres-Studie (2020–2025) von Peter Gloor zeigt, dass Pflanzen bioelektrische Signale als Frühwarnsystem gegen Herbivoren erzeugen. Ein ResNet50-Modell klassifiziert menschliche Emotionszustände via Pflanzenspannungs-Spektrogramme mit 97 % Genauigkeit.',
        abstract: 'Umfassende 5-Jahres-Untersuchung der elektromagnetischen Kommunikation zwischen Mensch und Pflanze: von der Detektion menschlicher Präsenz bis zur Schlafphasen-Klassifikation via bioelektrischer Pflanzensignale.',
    },

    // ─── Plant Electrophysiology Fundamentals ─────────────────
    {
        id: 'wound-signals-2022',
        title: 'Wound-Induced Systemic Responses and Their Coordination by Electrical Signals',
        authors: 'Hander T, Fernández-Calvo P, Stolze SC, et al.',
        year: 2022,
        journal: 'Frontiers in Plant Science',
        url: 'https://www.frontiersin.org/articles/10.3389/fpls.2022.880680/full',
        doi: '10.3389/fpls.2022.880680',
        category: 'fundamentals',
        categoryLabel: 'Grundlagen',
        greenmindLink: 'Diese Review erklärt, wie Pflanzen nach Verwundung systemische Signale über Aktionspotenziale und langsame Wellenpotenziale koordinieren. Für GreenMind liefert sie die wissenschaftliche Basis, warum bioelektrische Signale Stress anzeigen, bevor sichtbare Symptome auftreten.',
        abstract: 'Übersicht über verwundungsinduzierte systemische Signalwege in Pflanzen, einschliesslich der Rolle von Glutamat-Rezeptor-ähnlichen Kanälen (GLRs) und H⁺-ATPasen bei der Ausbreitung elektrischer Signale.',
    },
    {
        id: 'photosynthesis-electrical-2023',
        title: 'Hyperpolarization Electrical Signals Induced by Local Action of Moderate Heating Influence Photosynthetic Light Reactions in Wheat Plants',
        authors: 'Sukhov V, Surova L, Sherstneva O, et al.',
        year: 2023,
        journal: 'Frontiers in Plant Science',
        url: 'https://www.frontiersin.org/articles/10.3389/fpls.2023.1222367/full',
        doi: '10.3389/fpls.2023.1222367',
        category: 'fundamentals',
        categoryLabel: 'Grundlagen',
        greenmindLink: 'Lokale Erwärmung löst elektrische Hyperpolarisationssignale aus, die photosynthetische Lichtreaktionen in Weizen direkt beeinflussen. Für GreenMind bedeutet dies: Temperaturstress ist über bioelektrische Signale messbar und seine Auswirkung auf den Ertrag vorhersagbar.',
        abstract: 'Aktions- und Variationspotenziale teilen einen gemeinsamen Mechanismus über transiente Inaktivierung der H⁺-ATPase, die photosynthetische Reaktionen in Weizenpflanzen beeinflusst.',
    },

    // ─── Stress Detection via Plant Signals ───────────────────
    {
        id: 'drought-photosynthesis-2022',
        title: 'Electrical Signals in Leaves as Indicators of Drought Stress and Their Role in Photosynthetic Response',
        authors: 'Vodeneev V, Mudrilov M, Akinchits E, et al.',
        year: 2022,
        journal: 'Frontiers in Plant Science',
        url: 'https://www.frontiersin.org/articles/10.3389/fpls.2022.851883/full',
        doi: '10.3389/fpls.2022.851883',
        category: 'stress-detection',
        categoryLabel: 'Stress-Detektion',
        greenmindLink: 'Moderater Trockenstress verstärkt die Amplitude elektrischer Signale in Blättern, während schwerer Stress sie unterdrückt. Dieses Signaturmuster ermöglicht es GreenMind, den Schweregrad von Wasserstress quantitativ zu erfassen.',
        abstract: 'Untersuchung des Einflusses von moderatem und schwerem Trockenstress auf die elektrische Signalausbreitung und photosynthetische Reaktionen in Pflanzenblättern.',
    },
    {
        id: 'bioimpedance-drought-2023',
        title: 'Bioimpedance Measurements for Early Detection of Drought Stress in Plants',
        authors: 'Martinsen ØG, Kalvøy H, Grimnes S, et al.',
        year: 2023,
        journal: 'Scientific Reports',
        url: 'https://doi.org/10.1038/s41598-023-41051-4',
        doi: '10.1038/s41598-023-41051-4',
        category: 'stress-detection',
        categoryLabel: 'Stress-Detektion',
        greenmindLink: 'Bioimpedanz-Messungen erkennen Trockenstress in Pflanzen innerhalb von 60 Minuten — lange bevor Welke sichtbar wird. Dies validiert GreenMinds Ansatz, bioelektrische Pflanzensignale als Frühwarnsystem einzusetzen.',
        abstract: 'Bioimpedanz-Messungen an Pflanzengewebe zeigen signifikante Änderungen im extrazellulären Widerstand innerhalb einer Stunde nach induziertem Trockenstress.',
    },

    // ─── Hardware & Sensor Technology ─────────────────────────
    {
        id: 'venus-flytrap-2023',
        title: 'Plant Electrophysiology with Conformable Organic Electronics: Deciphering the Propagation of Venus Flytrap Action Potentials',
        authors: 'Ohayon D, Druet V, Vetter M, et al.',
        year: 2023,
        journal: 'Science Advances',
        url: 'https://doi.org/10.1126/sciadv.adh4443',
        doi: '10.1126/sciadv.adh4443',
        category: 'hardware',
        categoryLabel: 'Hardware & Sensorik',
        greenmindLink: 'Flexible organische Multielectrode-Arrays kartieren Aktionspotenziale der Venusfliegenfalle mit hoher Auflösung. Die Studie beweist, dass sich bioelektrische Pflanzensignale präzise und nicht-invasiv messen lassen — die technische Grundlage für GreenMinds Sensorik.',
        abstract: 'Entwicklung konformer organischer Transistor-Arrays (OECTs) für die grossflächige Kartierung von Aktionspotenzialen in Pflanzengewebe mit hoher räumlicher und zeitlicher Auflösung.',
    },
    {
        id: 'open-source-platform-2021',
        title: 'An Open-Source Platform for Plant Electrophysiology',
        authors: 'Pereira DR, Papa JP, Saraiva GFR, Souza GM',
        year: 2021,
        journal: 'Plant Methods',
        url: 'https://doi.org/10.1186/s13007-021-00727-y',
        doi: '10.1186/s13007-021-00727-y',
        category: 'hardware',
        categoryLabel: 'Hardware & Sensorik',
        greenmindLink: 'Diese Open-Source-Plattform für Pflanzenelektrophysiologie zeigt, wie kostengünstige Mikrocontroller (PSoC) bioelektrische Signale drahtlos in Feldumgebungen erfassen — genau der Ansatz, den GreenMind mit ESP32-basierten Sensoren verfolgt.',
        abstract: 'Open-Source-Plattform basierend auf Programmable System-on-Chip zur drahtlosen Erfassung bioelektrischer Pflanzensignale ohne Faraday-Käfig in Feldumgebungen.',
    },

    // ─── ML & Predictive Analytics on Plant Signals ───────────
    {
        id: 'electrome-ml-2023',
        title: 'ML-based Plant Stress Detection from IoT-sensed Reduced Electromes',
        authors: 'Bevilacqua M, De Marcellis A, et al.',
        year: 2023,
        journal: 'Computers and Electronics in Agriculture',
        url: 'https://doi.org/10.1016/j.compag.2023.107807',
        doi: '10.1016/j.compag.2023.107807',
        category: 'predictive-analytics',
        categoryLabel: 'Prädiktive Analytik',
        greenmindLink: 'IoT-Sensoren erfassen pflanzliche Elektrome (elektrische Signalmuster), die mit KNN-, SVM- und ANN-Algorithmen klassifiziert werden, um Pflanzenstress automatisiert zu erkennen. Dies liefert das analytische Rahmenwerk für GreenMinds Stresserkennung.',
        abstract: 'Ansatz zur Reduktion und Klassifikation von Zeitreihendaten pflanzlicher Elektrome mittels IoT-Geräten und Machine-Learning-Algorithmen (KNN, SVM, ANN).',
    },
];

export const CATEGORY_OPTIONS = [
    { value: 'all', label: 'Alle Kategorien' },
    { value: 'fundamentals', label: 'Grundlagen' },
    { value: 'stress-detection', label: 'Stress-Detektion' },
    { value: 'hardware', label: 'Hardware & Sensorik' },
    { value: 'predictive-analytics', label: 'Prädiktive Analytik' },
];
