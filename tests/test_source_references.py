from nima_semantica.retrieval import expand_tex_references


def region(ident, ordinal, text, document="paper"):
    return {"id": ident, "ordinal": ordinal, "text": text, "document_id": document}


def test_definition_and_neighbors_are_retrieved_with_provenance():
    claim = region("claim", 8, r"Use \cref{definition}.")
    definition = region("definition", 1, r"\label{definition} The actual hypotheses.")
    following = region("continuation", 2, "Continuation of definition.")
    other = region("other", 0, r"\label{definition} A different paper.", "other-paper")
    result, evidence = expand_tex_references([claim], [claim, definition, following, other])
    assert [item["id"] for item in result] == ["claim", "definition", "continuation"]
    assert result[1]["text"] == definition["text"]
    assert evidence["edges"][0]["label"] == "definition"


def test_expansion_is_bounded_and_records_omissions():
    claim = region("claim", 8, r"\ref{definition}")
    definition = region("definition", 1, r"\label{definition}")
    result, evidence = expand_tex_references([claim], [claim, definition], max_additional=0)
    assert result == [claim] and evidence["omitted"] == ["definition"]


def test_missing_duplicate_and_commented_references():
    claim = region("claim", 8, "\\cref{duplicate,missing}\n% \\ref{commented}")
    result, evidence = expand_tex_references([claim], [claim,
        region("a", 1, r"\label{duplicate}"), region("b", 5, r"\label{duplicate}")])
    assert result == [claim]
    assert {item["reason"] for item in evidence["unresolved"]} == {"missing", "ambiguous"}
    assert all(item["label"] != "commented" for item in evidence["unresolved"])


def test_reference_cycle_terminates():
    first = region("a", 0, r"\label{a} \ref{b}")
    second = region("b", 4, r"\label{b} \ref{a}")
    result, evidence = expand_tex_references([first], [first, second])
    assert len(result) == 2 and len(evidence["edges"]) == 1


def test_local_definitions_are_included_without_an_explicit_reference():
    claim = region("claim", 8, "A claim using a previously defined symbol.")
    definition = region("definition", 1, r"\begin{definition} Definition of that symbol.")
    result, evidence = expand_tex_references([claim], [claim, definition])
    assert [item["id"] for item in result] == ["claim", "definition"]
    assert evidence["edges"][0]["reason"] == "source-definition-environment"
