"""Fixed isolated integer search program and independent exact witness recheck."""
import inspect
import json

from .models import identity
from .research_additions import check_integer_witness, _independently_refutes
from .verification import evaluate, holds


def search_source(encoding):
    """Only validated AST data enters the program; no model-authored executable."""
    payload = encoding.model_dump(mode="json")
    return '''import json
from itertools import product, islice
from types import SimpleNamespace
''' + inspect.getsource(evaluate) + '\n' + inspect.getsource(holds) + '''
def obj(value):
    if isinstance(value, dict):
        return SimpleNamespace(**{k:obj(v) for k,v in value.items()})
    return value
task = json.loads(''' + repr(json.dumps(payload)) + ''')
claim = obj(task['claim'])
assumptions = [obj(a) for a in task['assumptions']]
visited = eligible = 0
witness = None
for values in islice(product(range(task['lower'], task['upper']+1), repeat=len(claim.variables)), task['max_evaluations']):
    visited += 1
    assignment = dict(zip(claim.variables, values))
    if not all(holds(a, assignment) for a in assumptions):
        continue
    eligible += 1
    if not holds(claim, assignment):
        witness = assignment
        break
print(json.dumps({'encoding_id':''' + repr(identity(encoding)) + ''',
    'witness':witness, 'evaluations':visited, 'eligible_evaluations':eligible,
    'box_size':(task['upper']-task['lower']+1)**len(claim.variables)}))
'''


def validate_search_output(encoding, output):
    """Check protocol and finite-domain counts before accepting an observation."""
    expected = (encoding.upper - encoding.lower + 1) ** len(encoding.claim.variables)
    if set(output) != {"encoding_id", "witness", "evaluations", "eligible_evaluations", "box_size"}:
        raise ValueError("invalid search output fields")
    if output["encoding_id"] != identity(encoding) or type(output["box_size"]) is not int or output["box_size"] != expected:
        raise ValueError("search output identifies another encoding/domain")
    visited, eligible = output["evaluations"], output["eligible_evaluations"]
    limit = min(expected, encoding.max_evaluations)
    if type(visited) is not int or type(eligible) is not int or not 0 <= eligible <= visited <= limit:
        raise ValueError("invalid search coverage counts")
    if output["witness"] is None and visited != limit:
        raise ValueError("search stopped early without a witness")
    if output["witness"] is not None and (not isinstance(output["witness"], dict) or eligible < 1):
        raise ValueError("invalid witness observation")
    return {**output, "box_exhausted":output["witness"] is None and visited == expected,
        "truncated":output["witness"] is None and visited < expected,
        "method":"lexicographic_integer_box", "lower":encoding.lower, "upper":encoding.upper}


def recheck_witness(encoding, observation):
    checked = check_integer_witness({"task":{"claim":encoding.claim.model_dump(mode="json"),
        "lower":encoding.lower, "upper":encoding.upper, "max_evaluations":encoding.max_evaluations},
        "claim_id":identity(encoding.claim), "witness":observation["witness"]})
    if not checked["verified_refutation"]:
        raise ValueError("candidate does not refute the encoded claim")
    if any(_independently_refutes(a, observation["witness"])[0] for a in encoding.assumptions):
        raise ValueError("candidate violates an encoded assumption")
    return {"validated":True, "findings":checked["findings"],
        "assumptions_checked":len(encoding.assumptions), "encoding_id":identity(encoding)}
