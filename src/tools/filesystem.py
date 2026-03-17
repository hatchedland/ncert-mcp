"""
Phase 1 MCP tools — file-system only, no database required.

Tools:
  list_books          — all available textbooks (optionally filtered by grade/subject)
  get_chapter         — full extracted text of one chapter
  get_chapter_metadata — metadata sidecar only (fast, no PDF parse)
  list_topics         — chapter titles / TOC for a book
  search_chapters     — BM25 keyword search across all chapters
"""

import json
import sys
from pathlib import Path

# Allow imports from scripts/
sys.path.insert(0, str(Path(__file__).parent.parent))

from config import NCERT_PDF_DIR
from text_cache import extract_text, extract_page_count

# Keep in sync with ingest.py
NCERT_TEXTBOOK_CHAPTERS: dict[tuple[int, str], tuple[str, int]] = {
    (7, "Mathematics"):    ("gemh1",  13),
    (7, "Science"):        ("gesc1",  13),
    (7, "Geography"):      ("gess1",   8),
    (7, "History"):        ("gess2",   7),
    (7, "Civics"):         ("gess3",   8),
    (8, "Mathematics"):    ("hemh1",  13),
    (8, "Science"):        ("hesc1",  13),
    (8, "Geography"):      ("hess2",   8),
    (8, "History"):        ("hess3",   8),
    (8, "Civics"):         ("hess4",   5),
    (9, "Mathematics"):    ("iemh1",  12),
    (9, "Science"):        ("iesc1",  12),
    (9, "Geography"):      ("iess1",   6),
    (9, "History"):        ("iess2",   4),
    (9, "Civics"):         ("iess3",   5),
    (9, "Economics"):      ("iess4",   5),
    (10, "Mathematics"):   ("jemh1",  14),
    (10, "Science"):       ("jesc1",  13),
    (10, "Geography"):     ("jess1",   7),
    (10, "History"):       ("jess2",   5),
    (10, "Civics"):        ("jess3",   5),
    (10, "Economics"):     ("jess4",   5),
    (11, "Mathematics"):   ("kemh1",  14),
    (11, "Physics_1"):     ("keph1",   7),
    (11, "Physics_2"):     ("keph2",   7),
    (11, "Chemistry_1"):   ("kech1",   6),
    (11, "Chemistry_2"):   ("kech2",   3),
    (11, "Biology"):       ("kebo1",  19),
    (11, "History"):       ("kehs1",   7),
    (11, "Geography_1"):   ("kehp1",  11),
    (11, "Civics"):        ("kecs1",  11),
    (12, "Mathematics_1"): ("lemh1",   6),
    (12, "Mathematics_2"): ("lemh2",   7),
    (12, "Physics_1"):     ("leph1",   8),
    (12, "Physics_2"):     ("leph2",   6),
    (12, "Chemistry_1"):   ("lech1",   5),
    (12, "Chemistry_2"):   ("lech2",   5),
    (12, "Biology"):       ("lebo1",  13),
    (12, "History"):       ("lehs1",   4),
    (12, "Civics"):        ("lecs1",  13),
}

# Hardcoded chapter titles (NCERT PDFs use a broken font encoding that makes
# auto-extraction unreliable). These are stable official titles.
CHAPTER_TITLES: dict[str, list[str]] = {
    # Grade 7
    "gemh1": ["Integers","Fractions and Decimals","Data Handling","Simple Equations",
               "Lines and Angles","The Triangle and its Properties","Congruence of Triangles",
               "Comparing Quantities","Rational Numbers","Practical Geometry",
               "Perimeter and Area","Algebraic Expressions","Exponents and Powers"],
    "gesc1": ["Nutrition in Plants","Nutrition in Animals","Fibre to Fabric","Heat",
               "Acids, Bases and Salts","Physical and Chemical Changes",
               "Weather, Climate and Adaptations of Animals to Climate","Winds, Storms and Cyclones",
               "Soil","Respiration in Organisms","Transportation in Animals and Plants",
               "Reproduction in Plants","Motion and Time"],
    "gess1": ["Environment","Inside Our Earth","Our Changing Earth","Air","Water",
               "Natural Vegetation and Wild Life","Human Environment: Settlement, Transport and Communication",
               "Human-Environment Interactions: The Tropical and the Subtropical Region"],
    "gess2": ["Tracing Changes Through a Thousand Years","New Kings and Kingdoms","The Delhi Sultans",
               "The Mughal Empire","Rulers and Buildings","Towns, Traders and Craftspersons",
               "Tribes, Nomads and Settled Communities"],
    "gess3": ["On Equality","Role of the Government in Health","How the State Government Works",
               "Growing up as Boys and Girls","Women Change the World","Understanding Media",
               "Understanding Advertising","Markets Around Us"],
    # Grade 8
    "hemh1": ["Rational Numbers","Linear Equations in One Variable","Understanding Quadrilaterals",
               "Data Handling","Squares and Square Roots","Cubes and Cube Roots",
               "Comparing Quantities","Algebraic Expressions and Identities","Mensuration",
               "Exponents and Powers","Direct and Inverse Proportions","Factorisation",
               "Introduction to Graphs"],
    "hesc1": ["Crop Production and Management","Microorganisms: Friend and Foe",
               "Coal and Petroleum","Combustion and Flame","Conservation of Plants and Animals",
               "Reproduction in Animals","Reaching the Age of Adolescence","Force and Pressure",
               "Friction","Sound","Chemical Effects of Electric Current",
               "Some Natural Phenomena","Light"],
    "hess2": ["Resources","Land, Soil, Water, Natural Vegetation and Wildlife Resources",
               "Mineral and Power Resources","Agriculture","Industries",
               "Human Resources","Conservation of Natural Resources","Human-Environment Interaction"],
    "hess3": ["How, When and Where","From Trade to Territory","Ruling the Countryside",
               "Tribals, Dikus and the Vision of a Golden Age","When People Rebel",
               "Weavers, Iron Smelters and Factory Owners","Civilising the 'Native', Educating the Nation",
               "Women, Caste and Reform"],
    "hess4": ["The Indian Constitution","Understanding Secularism","Parliament and the Making of Laws",
               "Judiciary","Understanding Marginalisation","Confronting Marginalisation"],
    # Grade 9
    "iemh1": ["Number Systems","Polynomials","Coordinate Geometry","Linear Equations in Two Variables",
               "Introduction to Euclid's Geometry","Lines and Angles","Triangles","Quadrilaterals",
               "Circles","Heron's Formula","Surface Areas and Volumes","Statistics"],
    "iesc1": ["Matter in Our Surroundings","Is Matter Around Us Pure","Atoms and Molecules",
               "Structure of the Atom","The Fundamental Unit of Life","Tissues",
               "Motion","Force and Laws of Motion","Gravitation","Work and Energy","Sound",
               "Improvement in Food Resources"],
    "iess1": ["India: Size and Location","Physical Features of India","Drainage","Climate",
               "Natural Vegetation and Wild Life","Population"],
    "iess2": ["The French Revolution","Socialism in Europe and the Russian Revolution",
               "Nazism and the Rise of Hitler","Forest Society and Colonialism"],
    "iess3": ["What is Democracy? Why Democracy?","Constitutional Design",
               "Electoral Politics","Working of Institutions","Democratic Rights"],
    "iess4": ["The Story of Village Palampur","People as Resource",
               "Poverty as a Challenge","Food Security in India",""],
    # Grade 10
    "jemh1": ["Real Numbers","Polynomials","Pair of Linear Equations in Two Variables",
               "Quadratic Equations","Arithmetic Progressions","Triangles",
               "Coordinate Geometry","Introduction to Trigonometry",
               "Some Applications of Trigonometry","Circles","Areas Related to Circles",
               "Surface Areas and Volumes","Statistics","Probability"],
    "jesc1": ["Chemical Reactions and Equations","Acids, Bases and Salts",
               "Metals and Non-metals","Carbon and Its Compounds",
               "Life Processes","Control and Coordination",
               "How do Organisms Reproduce?","Heredity","Light: Reflection and Refraction",
               "Human Eye and the Colourful World","Electricity",
               "Magnetic Effects of Electric Current","Our Environment"],
    "jess1": ["Resources and Development","Forest and Wildlife Resources","Water Resources",
               "Agriculture","Minerals and Energy Resources","Manufacturing Industries",
               "Lifelines of National Economy"],
    "jess2": ["The Rise of Nationalism in Europe","Nationalism in India",
               "The Making of a Global World","The Age of Industrialisation","Print Culture and the Modern World"],
    "jess3": ["Power Sharing","Federalism","Gender, Religion and Caste",
               "Political Parties","Outcomes of Democracy"],
    "jess4": ["Development","Sectors of the Indian Economy","Money and Credit",
               "Globalisation and the Indian Economy","Consumer Rights"],
    # Grade 11 — partial (titles for confirmed books)
    "kemh1": ["Sets","Relations and Functions","Trigonometric Functions","Complex Numbers and Quadratic Equations",
               "Linear Inequalities","Permutations and Combinations","Binomial Theorem",
               "Sequences and Series","Straight Lines","Conic Sections",
               "Introduction to Three Dimensional Geometry","Limits and Derivatives","Statistics","Probability"],
    "keph1": ["Physical World","Units and Measurements","Motion in a Straight Line",
               "Motion in a Plane","Laws of Motion","Work, Energy and Power","Systems of Particles and Rotational Motion"],
    "keph2": ["Gravitation","Mechanical Properties of Solids","Mechanical Properties of Fluids",
               "Thermal Properties of Matter","Thermodynamics","Kinetic Theory","Oscillations","Waves"],
    "kech1": ["Some Basic Concepts of Chemistry","Structure of Atom","Classification of Elements and Periodicity in Properties",
               "Chemical Bonding and Molecular Structure","Thermodynamics","Equilibrium"],
    "kech2": ["Redox Reactions","Organic Chemistry: Some Basic Principles and Techniques","Hydrocarbons"],
    "kebo1": ["The Living World","Biological Classification","Plant Kingdom","Animal Kingdom",
               "Morphology of Flowering Plants","Anatomy of Flowering Plants","Structural Organisation in Animals",
               "Cell: The Unit of Life","Biomolecules","Cell Cycle and Cell Division",
               "Photosynthesis in Higher Plants","Respiration in Plants","Plant Growth and Development",
               "Breathing and Exchange of Gases","Body Fluids and Circulation",
               "Excretory Products and their Elimination","Locomotion and Movement",
               "Neural Control and Coordination","Chemical Coordination and Integration"],
    "kehs1": ["From the Beginning of Time","Writing and City Life","An Empire Across Three Continents",
               "The Central Islamic Lands","Nomadic Empires","The Three Orders",
               "Changing Cultural Traditions"],
    "kehp1": ["India: Location","Structure and Physiography","Drainage System","Climate","Natural Vegetation",
               "Soils","Natural Hazards and Disasters","Industries",
               "Planning and Sustainable Development in Indian Context",
               "Transport and Communication","International Trade"],
    "kecs1": ["Political Theory: An Introduction","Freedom","Equality","Social Justice","Rights",
               "Citizenship","Nationalism","Secularism","Peace",
               "Development","Constitution: Why and How?"],
    # Grade 12
    "lemh1": ["Relations and Functions","Inverse Trigonometric Functions","Matrices",
               "Determinants","Continuity and Differentiability","Application of Derivatives"],
    "lemh2": ["Integrals","Application of Integrals","Differential Equations",
               "Vector Algebra","Three Dimensional Geometry","Linear Programming","Probability"],
    "leph1": ["Electric Charges and Fields","Electrostatic Potential and Capacitance",
               "Current Electricity","Moving Charges and Magnetism","Magnetism and Matter",
               "Electromagnetic Induction","Alternating Current","Electromagnetic Waves"],
    "leph2": ["Ray Optics and Optical Instruments","Wave Optics",
               "Dual Nature of Radiation and Matter","Atoms","Nuclei",
               "Semiconductor Electronics: Materials, Devices and Simple Circuits"],
    "lech1": ["The Solid State","Solutions","Electrochemistry","Chemical Kinetics","Surface Chemistry"],
    "lech2": ["General Principles and Processes of Isolation of Elements",
               "The p-Block Elements","The d and f Block Elements",
               "Coordination Compounds","Haloalkanes and Haloarenes"],
    "lebo1": ["Reproduction in Organisms","Sexual Reproduction in Flowering Plants",
               "Human Reproduction","Reproductive Health","Principles of Inheritance and Variation",
               "Molecular Basis of Inheritance","Evolution","Human Health and Disease",
               "Strategies for Enhancement in Food Production","Microbes in Human Welfare",
               "Biotechnology: Principles and Processes","Biotechnology and its Applications",
               "Organisms and Populations"],
    "lehs1": ["Bricks, Beads and Bones: The Harappan Civilisation","Kings, Farmers and Towns: Early States and Economies",
               "Kinship, Caste and Class: Early Societies","Thinkers, Beliefs and Buildings: Cultural Developments"],
    "lecs1": ["Challenges of Nation Building","Era of One-Party Dominance","Politics of Planned Development",
               "India's External Relations","Challenges to and Restoration of the Congress System",
               "The Crisis of Democratic Order","Rise of Popular Movements",
               "Regional Aspirations","Recent Developments in Indian Politics",
               "Democratic Upsurge and Coalition Politics","Planning and Development",
               "India's Relations with its Neighbours","Security in Contemporary World"],
}


def _pdf_path(grade: int, subject: str, code: str, chapter: int) -> Path:
    return NCERT_PDF_DIR / f"grade_{grade}" / subject / f"{code}{chapter:02d}.pdf"


def _meta_path(grade: int, subject: str, code: str, chapter: int) -> Path:
    filename = f"{code}{chapter:02d}.pdf"
    return NCERT_PDF_DIR / f"grade_{grade}" / subject / f"{filename}.meta.json"


# ── Tool implementations ──────────────────────────────────────────────────────

def list_books(grade: int | None = None, subject: str | None = None) -> list[dict]:
    """List all textbooks available on disk, optionally filtered."""
    results = []
    for (g, s), (code, num_chapters) in NCERT_TEXTBOOK_CHAPTERS.items():
        if grade is not None and g != grade:
            continue
        if subject is not None and s.lower() != subject.lower():
            continue

        # Count chapters actually on disk
        on_disk = sum(
            1 for ch in range(1, num_chapters + 1)
            if _pdf_path(g, s, code, ch).exists()
        )
        results.append({
            "grade": g,
            "subject": s,
            "book_code": code,
            "num_chapters": num_chapters,
            "chapters_on_disk": on_disk,
            "local_dir": str(NCERT_PDF_DIR / f"grade_{g}" / s),
        })

    results.sort(key=lambda x: (x["grade"], x["subject"]))
    return results


def get_chapter(grade: int, subject: str, chapter: int) -> dict:
    """Return full extracted text and metadata for one chapter."""
    entry = NCERT_TEXTBOOK_CHAPTERS.get((grade, subject))
    if not entry:
        raise ValueError(f"No mapping for grade={grade} subject={subject}")

    code, num_chapters = entry
    if not 1 <= chapter <= num_chapters:
        raise ValueError(f"Chapter {chapter} out of range (1–{num_chapters})")

    pdf = _pdf_path(grade, subject, code, chapter)
    if not pdf.exists():
        raise FileNotFoundError(f"PDF not on disk: {pdf}")

    meta_file = _meta_path(grade, subject, code, chapter)
    meta = json.loads(meta_file.read_text()) if meta_file.exists() else {}

    text = extract_text(pdf)
    page_count = extract_page_count(pdf)

    return {
        "grade": grade,
        "subject": subject,
        "chapter": chapter,
        "book_code": code,
        "page_count": page_count,
        "source_url": meta.get("url", ""),
        "downloaded_at": meta.get("downloaded_at", ""),
        "text": text,
    }


def get_chapter_metadata(grade: int, subject: str, chapter: int) -> dict:
    """Return just the metadata sidecar — fast, no PDF parsing."""
    entry = NCERT_TEXTBOOK_CHAPTERS.get((grade, subject))
    if not entry:
        raise ValueError(f"No mapping for grade={grade} subject={subject}")

    code, _ = entry
    meta_file = _meta_path(grade, subject, code, chapter)
    if not meta_file.exists():
        raise FileNotFoundError(f"Metadata not found: {meta_file}")

    return json.loads(meta_file.read_text())


def list_topics(grade: int, subject: str) -> list[dict]:
    """Return chapter numbers and titles for a textbook."""
    entry = NCERT_TEXTBOOK_CHAPTERS.get((grade, subject))
    if not entry:
        raise ValueError(f"No mapping for grade={grade} subject={subject}")

    code, num_chapters = entry
    titles = CHAPTER_TITLES.get(code, [])
    topics = []
    for ch in range(1, num_chapters + 1):
        pdf = _pdf_path(grade, subject, code, ch)
        title = titles[ch - 1] if ch - 1 < len(titles) else f"Chapter {ch}"
        topics.append({"chapter": ch, "title": title, "on_disk": pdf.exists()})
    return topics


def search_chapters(
    query: str,
    grade: int | None = None,
    subject: str | None = None,
    top_k: int = 5,
) -> list[dict]:
    """BM25 keyword search across all downloaded chapter PDFs."""
    from rank_bm25 import BM25Okapi

    # Collect candidate chapters
    candidates = []
    for (g, s), (code, num_chapters) in NCERT_TEXTBOOK_CHAPTERS.items():
        if grade is not None and g != grade:
            continue
        if subject is not None and s.lower() != subject.lower():
            continue
        for ch in range(1, num_chapters + 1):
            pdf = _pdf_path(g, s, code, ch)
            if pdf.exists():
                candidates.append((g, s, ch, pdf))

    if not candidates:
        return []

    # Extract / load cached texts
    corpus_tokens = []
    for _, _, _, pdf in candidates:
        text = extract_text(pdf)
        corpus_tokens.append(text.lower().split())

    bm25 = BM25Okapi(corpus_tokens)
    query_tokens = query.lower().split()
    scores = bm25.get_scores(query_tokens)

    # Rank and return top_k
    ranked = sorted(
        zip(scores, candidates), key=lambda x: x[0], reverse=True
    )[:top_k]

    results = []
    for score, (g, s, ch, pdf) in ranked:
        if score == 0:
            break
        text = extract_text(pdf)
        # Find a relevant snippet around the first query word hit
        lower_text = text.lower()
        pos = lower_text.find(query_tokens[0])
        if pos >= 0:
            start = max(0, pos - 100)
            end = min(len(text), pos + 300)
            snippet = text[start:end].replace("\n", " ").strip()
        else:
            snippet = text[:300].replace("\n", " ").strip()

        results.append({
            "grade": g,
            "subject": s,
            "chapter": ch,
            "score": round(float(score), 4),
            "snippet": snippet,
        })

    return results
