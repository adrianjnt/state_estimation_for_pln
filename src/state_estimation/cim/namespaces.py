"""CIM and CGMES namespace constants per IEC 61970-301 / IEC 61970-552."""

from rdflib import Namespace, URIRef

CIM_URI  = "http://iec.ch/TC57/CIM100#"
RDF_URI  = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
RDFS_URI = "http://www.w3.org/2000/01/rdf-schema#"
OWL_URI  = "http://www.w3.org/2002/07/owl#"
MD_URI   = "http://iec.ch/TC57/61970-552/ModelDescription/1#"
EUMD_URI = "http://iec.ch/TC57/61970-552/ModelDescription/1#"
XSD_URI  = "http://www.w3.org/2001/XMLSchema#"
SH_URI   = "http://www.w3.org/ns/shacl#"

CIM  = Namespace(CIM_URI)
MD   = Namespace(MD_URI)

# ---------------------------------------------------------------------------
# Legacy CIM namespace URIs — used by the version-aliasing preprocessor
# ---------------------------------------------------------------------------

# CIM14 (IEC 61970-301 edition 1 — common in older EMS exports)
CIM14_URI = "http://iec.ch/TC57/2009/CIM-schema-cim14#"
# CIM16 alternate prefix (some tools omit the year segment)
CIM16_SHORT_URI = "http://iec.ch/TC57/CIM-schema-cim16#"
# CIM16 (IEC 61970-301 edition 2 — ENTSO-E CGMES 2.4 era)
CIM16_URI = "http://iec.ch/TC57/2013/CIM-schema-cim16#"
# Some tools use a slightly different CIM16 path
CIM16_ALT_URI = "http://iec.ch/TC57/2011/CIM-schema-cim16#"

# Maps every known legacy namespace prefix → CIM100 equivalent.
# The parser replaces any predicate (or type) URI that starts with a key
# with a URI that uses CIM100_URI as prefix instead.
LEGACY_NS_ALIASES: dict[str, str] = {
    CIM14_URI:       CIM_URI,
    CIM16_URI:       CIM_URI,
    CIM16_SHORT_URI: CIM_URI,
    CIM16_ALT_URI:   CIM_URI,
}

# Predicate local-name renames across CIM versions.
# Key: (legacy_namespace, old_local_name) → new_local_name in CIM100.
# Applied after namespace substitution so the resulting URI is fully CIM100.
PREDICATE_RENAMES: dict[tuple[str, str], str] = {
    # CIM14: transformer windings were called TransformerWinding
    (CIM14_URI, "TransformerWinding"):              "PowerTransformerEnd",
    (CIM14_URI, "TransformerWinding.r"):            "PowerTransformerEnd.r",
    (CIM14_URI, "TransformerWinding.x"):            "PowerTransformerEnd.x",
    (CIM14_URI, "TransformerWinding.r0"):           "PowerTransformerEnd.r0",
    (CIM14_URI, "TransformerWinding.x0"):           "PowerTransformerEnd.x0",
    (CIM14_URI, "TransformerWinding.b"):            "PowerTransformerEnd.b",
    (CIM14_URI, "TransformerWinding.g"):            "PowerTransformerEnd.g",
    (CIM14_URI, "TransformerWinding.ratedS"):       "PowerTransformerEnd.ratedS",
    (CIM14_URI, "TransformerWinding.ratedU"):       "PowerTransformerEnd.ratedU",
    (CIM14_URI, "TransformerWinding.PowerTransformer"): "PowerTransformerEnd.PowerTransformer",
    (CIM14_URI, "TransformerWinding.Terminal"):     "TransformerEnd.Terminal",
    (CIM14_URI, "TransformerWinding.windingType"):  "TransformerEnd.endNumber",
    # CIM14: winding type was an enum; map numeric positions to endNumber integers
    # (handled specially in parser via _CIM14_WINDING_TYPE_TO_SEQ)
    # CIM16 → CIM100 renames (mostly identical local names; namespace change is enough)
    # but a few were formally renamed:
    (CIM16_URI, "Terminal.TopologicalNode"):        "TopologicalNode.Terminal",  # moved to TP
    (CIM16_SHORT_URI, "Terminal.TopologicalNode"):  "TopologicalNode.Terminal",
}

# CGMES profile URIs (used in md:Model.profile statements)
PROFILE_EQ  = "http://iec.ch/TC57/ns/CIM/CoreEquipment-EU/2.0"
PROFILE_TP  = "http://iec.ch/TC57/ns/CIM/Topology-EU/2.0"
PROFILE_SSH = "http://iec.ch/TC57/ns/CIM/SteadyStateHypothesis-EU/2.0"
PROFILE_SV  = "http://iec.ch/TC57/ns/CIM/StateVariables-EU/2.0"
PROFILE_DY  = "http://iec.ch/TC57/ns/CIM/Dynamics-EU/1.0"

# Map profile URI → short label
PROFILE_LABELS: dict[str, str] = {
    PROFILE_EQ:  "EQ",
    PROFILE_TP:  "TP",
    PROFILE_SSH: "SSH",
    PROFILE_SV:  "SV",
}

# Commonly used CIM predicate URIs
P_MRID        = URIRef(CIM_URI + "IdentifiedObject.mRID")
P_NAME        = URIRef(CIM_URI + "IdentifiedObject.name")
P_DESCRIPTION = URIRef(CIM_URI + "IdentifiedObject.description")
P_SEQ_NUM     = URIRef(CIM_URI + "ACDCTerminal.sequenceNumber")
P_CONNECTED   = URIRef(CIM_URI + "ACDCTerminal.connected")
P_OPEN        = URIRef(CIM_URI + "Switch.open")
P_NORMALOPEN  = URIRef(CIM_URI + "Switch.normalOpen")
P_RETAINED    = URIRef(CIM_URI + "Switch.retained")

XML_PREFIXES = {
    "rdf":  RDF_URI,
    "cim":  CIM_URI,
    "md":   MD_URI,
    "rdfs": RDFS_URI,
}
