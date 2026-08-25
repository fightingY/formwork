"""A small singly linked list."""


class Node:
    def __init__(self, value, next_node=None):
        self.value = value
        self.next = next_node


class LinkedList:
    def __init__(self, values=None):
        self.head = None
        for value in values or []:
            self.push(value)

    def push(self, value):
        self.head = Node(value, self.head)

    def pop(self):
        return None

    def reversed(self):
        return LinkedList()

    def __iter__(self):
        node = self.head
        while node is not None:
            yield node.value
            node = node.next
