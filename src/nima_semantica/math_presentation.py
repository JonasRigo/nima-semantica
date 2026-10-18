"""LaTeX presentation of recorded scalar expressions without eval or sympify."""
import ast

def expression_latex(value):
    if not isinstance(value,(str,int,float)) or len(str(value))>4096:return None
    try:
        import sympy as s
        tree=ast.parse(str(value),mode='eval')
        if len(list(ast.walk(tree)))>256:return None
        functions={name:getattr(s,name) for name in ('sin','cos','tan','exp','log','gamma','Piecewise','Abs')}
        def convert(n):
            if isinstance(n,ast.Name):return {'pi':s.pi,'oo':s.oo,'I':s.I,'True':s.true,'False':s.false}.get(n.id,s.Symbol(n.id))
            if isinstance(n,ast.Constant):
                if type(n.value) not in (int,float,bool) or len(str(n.value))>40:raise ValueError('Unsupported scalar')
                return s.sympify(n.value)
            if isinstance(n,(ast.Tuple,ast.List)):return tuple(convert(x) for x in n.elts)
            if isinstance(n,ast.UnaryOp) and isinstance(n.op,(ast.USub,ast.UAdd)):
                value=convert(n.operand);return s.Mul(-1,value,evaluate=False) if isinstance(n.op,ast.USub) else value
            if isinstance(n,ast.BinOp):
                a,b=convert(n.left),convert(n.right)
                if isinstance(n.op,ast.Add):return s.Add(a,b,evaluate=False)
                if isinstance(n.op,ast.Sub):return s.Add(a,s.Mul(-1,b,evaluate=False),evaluate=False)
                if isinstance(n.op,ast.Mult):return s.Mul(a,b,evaluate=False)
                if isinstance(n.op,ast.Div):return s.Mul(a,s.Pow(b,-1,evaluate=False),evaluate=False)
                if isinstance(n.op,ast.Pow):return s.Pow(a,b,evaluate=False)
            if isinstance(n,ast.Compare) and len(n.ops)==1:
                functions_cmp={ast.Lt:s.Lt,ast.LtE:s.Le,ast.Gt:s.Gt,ast.GtE:s.Ge,ast.Eq:s.Eq,ast.NotEq:s.Ne}
                return functions_cmp[type(n.ops[0])](convert(n.left),convert(n.comparators[0]),evaluate=False)
            if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and not n.keywords:
                args=[convert(x) for x in n.args]
                if n.func.id=='sqrt' and len(args)==1:return s.Pow(args[0],s.Rational(1,2),evaluate=False)
                if n.func.id in functions:return functions[n.func.id](*args,evaluate=False)
            raise ValueError('Unsupported presentation syntax')
        return s.latex(convert(tree.body))
    except (ValueError,TypeError,SyntaxError,KeyError,RecursionError,AttributeError):return None
