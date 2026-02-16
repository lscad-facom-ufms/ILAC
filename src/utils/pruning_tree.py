from itertools import combinations
from anytree import Node, RenderTree

class VariantNode(Node):
    """Nó na árvore de variantes, representando uma combinação de linhas modificadas."""
    def __init__(self, name, modified_lines=None, parent=None, status='PENDING', error=None, variant_hash=None, energy=None, cost=None):
        super().__init__(name, parent)
        self.modified_lines = tuple(sorted(modified_lines)) if modified_lines is not None else tuple()
        self.status = status  # PENDING, SIMULATING, COMPLETED, PRUNED, FAILED
        self.error = error
        self.variant_hash = variant_hash
        self.energy = energy  # Armazena a energia/tempo (profiling)
        self.cost = cost      # Custo heurístico calculado (peso erro + energia)

def build_variant_tree(modifiable_lines):
    """Constrói a árvore de variantes potenciais a partir de uma lista de números de linha modificáveis."""
    root = VariantNode("original", modified_lines=[])
    nodes = {(): root}

    # Usar tuplas ordenadas como chaves garante que combinações como (1, 2) e (2, 1) sejam tratadas como a mesma.
    for r in range(1, len(modifiable_lines) + 1):
        for combo in combinations(modifiable_lines, r):
            combo = tuple(sorted(combo))
            parent_combo = combo[:-1] # O pai é a combinação com um elemento a menos
            parent_node = nodes.get(parent_combo)
            
            if parent_node:
                node_name = "mod_" + "_".join(map(str, combo))
                node = VariantNode(node_name, modified_lines=list(combo), parent=parent_node)
                nodes[combo] = node
    return root

def prune_branch(node):
    """Poda um nó e todos os seus descendentes, marcando-os para não serem executados."""
    if node.status not in ['COMPLETED', 'FAILED']:
        node.status = 'PRUNED'
    for descendant in node.descendants:
        descendant.status = 'PRUNED'

def save_tree_to_file(root, filepath):
    """Salva a estrutura da árvore e o status em um arquivo de texto para visualização."""
    with open(filepath, 'w') as f:
        for pre, _, node in RenderTree(root):
            details_list = [f"status={node.status}"]
            
            if node.error is not None:
                details_list.append(f"error={node.error:.4f}")
            if getattr(node, 'energy', None) is not None and node.energy != float('inf'):
                details_list.append(f"energy={node.energy:.4f}")
            if getattr(node, 'cost', None) is not None:
                details_list.append(f"cost={node.cost:.4f}")
                
            details = ", ".join(details_list)

            # Verificação de segurança para o hash
            if node.variant_hash and len(node.variant_hash) >= 8:
                hash_info = f", hash={node.variant_hash[:8]}"
            elif node.variant_hash:
                hash_info = f", hash={node.variant_hash}"
            else:
                hash_info = ""
                
            f.write(f"{pre}{node.name} [{details}{hash_info}]\n")

def save_tree_to_dot(root, filepath):
    """Salva a árvore em um arquivo .dot para visualização com Graphviz."""
    from anytree.exporter import DotExporter

    def nodeattrfunc(node):
        # Usa a lista de linhas modificadas para o rótulo, ou 'original' para a raiz
        if node.is_root:
            node_id_str = "original"
        else:
            # Converte a tupla de linhas modificadas para uma string com formato de lista
            node_id_str = str(list(node.modified_lines))

        label = f"{node_id_str}\\nStatus: {node.status}"
        if node.error is not None:
            label += f"\\nError: {node.error:.4f}"
        if getattr(node, 'cost', None) is not None:
            label += f"\\nCost: {node.cost:.4f}"
        
        color = {
            'COMPLETED': 'lightgreen',
            'PRUNED': 'lightcoral',
            'FAILED': 'orangered',
            'PENDING': 'lightblue'
        }.get(node.status, 'gray')
        
        return f'label="{label}", style=filled, fillcolor={color}'

    exporter = DotExporter(root, nodeattrfunc=nodeattrfunc)
    exporter.to_dotfile(filepath)