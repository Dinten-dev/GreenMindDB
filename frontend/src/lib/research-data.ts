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
        id: 'gloor-human-movement-2025',
        title: 'Machine Learning Distinguishes Plant Bioelectric Recordings with and Without Nearby Human Movement',
        authors: 'Gloor PA, Weinbeer M',
        year: 2025,
        journal: 'Biosensors (MDPI)',
        url: 'https://pmc.ncbi.nlm.nih.gov/articles/PMC12649949/',
        doi: '10.3390/bios15120744',
        category: 'fundamentals',
        categoryLabel: 'Grundlagen',
        greenmindLink: 'Peter Gloor weist nach, dass Pflanzen messbare bioelektrische Unterschiede zeigen, wenn sich ein Mensch in der Nähe bewegt. Random-Forest-Klassifikatoren erreichen 62,7 % Genauigkeit bei 2978 Proben über drei Pflanzenarten — ein weiterer Beleg, dass bioelektrische Pflanzensignale auf Umgebungsveränderungen reagieren.',
        abstract: 'Bioelektrische Aufzeichnungen von 2978 Pflanzenproben (Basilikum, Salat, Tomate) werden mit Random Forest und CNN klassifiziert, um statistisch signifikante Unterschiede zwischen Aufnahmen mit und ohne menschliche Bewegung in der Nähe zu detektieren.',
    },
    {
        id: 'garlando-impedance-2022',
        title: 'Ask the Plants Directly: Understanding Plant Needs Using Electrical Impedance Measurements',
        authors: 'Garlando U, Calvo S, Barezzi M, Sanginario A, Motto Ros P, Demarchi D',
        year: 2022,
        journal: 'Computers and Electronics in Agriculture',
        url: 'https://doi.org/10.1016/j.compag.2022.106707',
        doi: '10.1016/j.compag.2022.106707',
        category: 'stress-detection',
        categoryLabel: 'Stress-Detektion',
        greenmindLink: 'Elektrische Impedanzmessungen am Pflanzenstamm erkennen Wasserstress mit bis zu 95 % Korrelation zur Bodenfeuchtigkeit — eine kostengünstige Methode, die Pflanzenbedürfnisse direkt zu erfassen, genau wie GreenMinds Sensor-Ansatz es vorsieht.',
        abstract: 'In-vivo Impedanzmessungen am Pflanzenstamm zeigen eine signifikante Korrelation mit Umweltparametern. Die Methode ermöglicht eine direkte, kostengünstige Echtzeit-Überwachung des Pflanzenzustands ohne visuelle Inspektion.',
    },
];

export const CATEGORY_OPTIONS = [
    { value: 'all', label: 'Alle Kategorien' },
    { value: 'fundamentals', label: 'Grundlagen' },
    { value: 'stress-detection', label: 'Stress-Detektion' },
    { value: 'hardware', label: 'Hardware & Sensorik' },
    { value: 'predictive-analytics', label: 'Prädiktive Analytik' },
];
