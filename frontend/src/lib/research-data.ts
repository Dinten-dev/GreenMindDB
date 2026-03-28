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
    {
        id: 'tran-nutrient-2024',
        title: 'Advanced Assessment of Nutrient Deficiencies in Greenhouse with Electrophysiological Signals',
        authors: 'Tran D, Najdenovska E, Dutoit F, Plummer C, Wallbridge N, Mazza M, Camps C, Raileanu LE',
        year: 2024,
        journal: 'Horticulture, Environment, and Biotechnology',
        url: 'https://doi.org/10.1007/s13580-023-00589-w',
        doi: '10.1007/s13580-023-00589-w',
        category: 'stress-detection',
        categoryLabel: 'Stress-Detektion',
        greenmindLink: 'Elektrophysiologische Signale werden eingesetzt, um Nährstoffmängel in Gewächshauspflanzen zu erkennen. Dies zeigt, dass GreenMinds Sensortechnologie nicht nur Wasserstress, sondern auch Nährstoffdefizite direkt an der Pflanze messen kann.',
        abstract: 'Einsatz elektrophysiologischer Signale zur fortgeschrittenen Bewertung von Nährstoffmängeln in Gewächshausumgebungen.',
    },
    {
        id: 'volkov-biosensors-2006',
        title: 'Plants as Environmental Biosensors',
        authors: 'Volkov AG, Ranatunga DRA',
        year: 2006,
        journal: 'Analytical Chemistry',
        url: 'https://pmc.ncbi.nlm.nih.gov/articles/PMC2635006/',
        doi: '10.1016/j.aca.2006.04.001',
        category: 'fundamentals',
        categoryLabel: 'Grundlagen',
        greenmindLink: 'Diese grundlegende Arbeit zeigt, dass Pflanzen über Aktions- und Variationspotenziale auf Umweltstress reagieren — von Schädlingsbefall über Pestizide bis zu Schwermetallen. Sie bildet die wissenschaftliche Basis für die Nutzung von Pflanzen als lebende Biosensoren, wie GreenMind es umsetzt.',
        abstract: 'Übersicht über die Nutzung von Pflanzen als schnelle Biosensoren für die Erkennung von Lichtrichtung, Insektenangriffen, Pestiziden, Entkopplern und Schwermetallbelastungen anhand elektrischer Signale.',
    },
    {
        id: 'he-heavy-metals-2024',
        title: 'Research Progress of the Detection and Analysis Methods of Heavy Metals in Plants',
        authors: 'He S, Niu Y, Xing L, Liang Z, Song X, Ding M, Huang W',
        year: 2024,
        journal: 'Frontiers in Plant Science',
        url: 'https://pmc.ncbi.nlm.nih.gov/articles/PMC10867983/',
        doi: '10.3389/fpls.2024.1310328',
        category: 'stress-detection',
        categoryLabel: 'Stress-Detektion',
        greenmindLink: 'Umfassende Übersicht über Methoden zur Schwermetalldetektion in Pflanzen, einschliesslich nicht-invasiver Messtechniken. Für GreenMind relevant, da Schwermetallbelastung pflanzliche Biomarker und elektrische Signale beeinflusst.',
        abstract: 'Systematische Übersicht der Analyse- und Detektionstechniken für Schwermetallkonzentrationen in Pflanzen, von ICP-MS über XRF bis zu nicht-invasiven Mikro-Testverfahren und Omics-Ansätzen.',
    },
    {
        id: 'phytonode-ml-2025',
        title: 'When Plants Respond: Electrophysiology and Machine Learning for Green Monitoring Systems',
        authors: 'Meder F, et al.',
        year: 2025,
        journal: 'arXiv Preprint',
        url: 'https://arxiv.org/html/2506.23872v1',
        doi: 'arXiv:2506.23872',
        category: 'predictive-analytics',
        categoryLabel: 'Prädiktive Analytik',
        greenmindLink: 'Hedera helix wird mit einem PhytoNode-Wearable ausgestattet, das über 5 Monate elektrophysiologische Signale im Freien erfasst. AutoML-Klassifikatoren erreichen bis zu 95 % F1-Score — ein Beweis, dass pflanzliche Elektrosignale auch unter realen Bedingungen für präzises Umweltmonitoring nutzbar sind.',
        abstract: 'Biohybrides System mit pflanzentragbarem Gerät (PhytoNode) zur kontinuierlichen Erfassung elektrophysiologischer Aktivität von Hedera helix. AutoML-Ansätze übertreffen manuelles Tuning mit bis zu 95 % F1-Score.',
    },
];

export const CATEGORY_OPTIONS = [
    { value: 'all', label: 'Alle Kategorien' },
    { value: 'fundamentals', label: 'Grundlagen' },
    { value: 'stress-detection', label: 'Stress-Detektion' },
    { value: 'hardware', label: 'Hardware & Sensorik' },
    { value: 'predictive-analytics', label: 'Prädiktive Analytik' },
];
