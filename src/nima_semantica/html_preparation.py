"""Offline, structure-aware HTML normalization; never fetch linked resources."""
from bs4 import BeautifulSoup


def normalize_html(text):
    soup = BeautifulSoup(text, "html.parser")
    diagnostics = []
    for element in soup(["script", "style", "nav", "footer"]):
        element.decompose()
    for formula in soup.find_all("math"):
        annotation = formula.find("annotation", attrs={"encoding": "application/x-tex"})
        latex = annotation.get_text() if annotation else formula.get("alttext")
        original = str(formula)
        diagnostics.append({"kind": "html_equation", "original": original, "latex": latex,
            "element_id": formula.get("id"), "status": "decoded" if latex else "unresolved"})
        formula.replace_with("\n\\[" + latex + "\\]\n" if latex else "\n" + original + "\n")
    for element in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6"]):
        diagnostics.append({"kind": "html_heading", "element_id": element.get("id"),
                            "level": int(element.name[1]), "text": element.get_text(" ", strip=True)})
        element.insert_before("\n" + "#" * int(element.name[1]) + " ")
    for link in soup.find_all("a", href=True):
        label, target = link.get_text(" ", strip=True), link["href"]
        diagnostics.append({"kind": "html_reference", "element_id": link.get("id"),
                            "target": target, "text": label})
        link.replace_with(f"[{label}]({target})")
    for image in soup.find_all("img"):
        diagnostics.append({"kind": "html_image", "element_id": image.get("id"),
            "alt": image.get("alt", ""), "source": image.get("src", ""), "status": "unresolved"})
        image.replace_with("[Image: " + image.get("alt", "not transcribed") + "]")
    body = soup.find("article") or soup.body or soup
    return body.get_text("\n"), diagnostics
