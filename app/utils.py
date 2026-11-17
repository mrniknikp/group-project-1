def dfs_iterative(graph, start):
    visited = set()
    stack = [start]
    
    while stack:
        vertex = stack.pop()
        if vertex not in visited:
            visited.add(vertex)
            stack.extend(graph[vertex] - visited)
            
    return visited

#Ебашим вызовы АПИ для разных авиакомпаний и получаем ответы. Составляем дерево по ответам и ищем лучшие варианты.
