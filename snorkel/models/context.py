from .meta import SnorkelBase, snorkel_postgres
from sqlalchemy import Column, String, Integer, Table, Text, ForeignKey, UniqueConstraint
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import relationship, backref
from sqlalchemy.types import PickleType


corpus_document_association = Table('corpus_document_association', SnorkelBase.metadata,
                                    Column('corpus_id', Integer, ForeignKey('corpus.id')),
                                    Column('document_id', Integer, ForeignKey('document.id'))
                                    )


class Corpus(SnorkelBase):
    """
    A set of Documents, uniquely identified by a name.

    Corpora have many-to-many relationships with Documents, so users can create
    subsets, supersets, etc.
    """
    __tablename__ = 'corpus'
    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)
    documents = relationship('Document', secondary=corpus_document_association)
    # TODO: What should the cascades be?

    def __repr__(self):
        return "Corpus (" + str(self.name) + ")"

    def __iter__(self):
        """Default iterator is over self.documents"""
        for doc in self.documents:
            yield doc

    def __eq__(self, other):
        return self is other

    def __ne__(self, other):
        return self is not other

    def __hash__(self):
        return id(self)


class Context(SnorkelBase):
    """
    A piece of content from which Candidates are composed.
    """
    __tablename__ = 'context'
    id = Column(Integer, primary_key=True)
    type = Column(String, nullable=False)
    stable_id = Column(String, unique=True, nullable=False)

    __mapper_args__ = {
        'polymorphic_identity': 'context',
        'polymorphic_on': type
    }

    def __eq__(self, other):
        return self is other

    def __ne__(self, other):
        return self is not other

    def __hash__(self):
        return id(self)


class Document(Context):
    """
    A root Context.
    """
    __tablename__ = 'document'
    id = Column(Integer, ForeignKey('context.id'), primary_key=True)
    name = Column(String, unique=True, nullable=False)
    meta = Column(PickleType)

    __mapper_args__ = {
        'polymorphic_identity': 'document',
    }

    def __repr__(self):
        return "Document " + str(self.name)


class Sentence(Context):
    """A sentence Context in a Document."""
    __tablename__ = 'sentence'
    id = Column(Integer, ForeignKey('context.id'), primary_key=True)
    document_id = Column(Integer, ForeignKey('document.id'))
    document = relationship('Document', backref=backref('sentences', cascade='all, delete-orphan'), foreign_keys=document_id)
    position = Column(Integer, nullable=False)
    text = Column(Text, nullable=False)
    if snorkel_postgres:
        words = Column(postgresql.ARRAY(String), nullable=False)
        char_offsets = Column(postgresql.ARRAY(Integer), nullable=False)
        lemmas = Column(postgresql.ARRAY(String))
        poses = Column(postgresql.ARRAY(String))
        dep_parents = Column(postgresql.ARRAY(Integer))
        dep_labels = Column(postgresql.ARRAY(String))
    else:
        words = Column(PickleType, nullable=False)
        char_offsets = Column(PickleType, nullable=False)
        lemmas = Column(PickleType)
        poses = Column(PickleType)
        dep_parents = Column(PickleType)
        dep_labels = Column(PickleType)

    __mapper_args__ = {
        'polymorphic_identity': 'sentence',
    }

    __table_args__ = (
        UniqueConstraint(document_id, position),
    )

    def _asdict(self):
        return {
            'id': self.id,
            'document': self.document,
            'position': self.position,
            'text': self.text,
            'words': self.words,
            'char_offsets': self.char_offsets,
            'lemmas': self.lemmas,
            'poses': self.poses,
            'dep_parents': self.dep_parents,
            'dep_labels': self.dep_labels
        }

    def __repr__(self):
        return "Sentence" + str((self.document, self.position, self.text))


class TemporarySpan(object):

    def __init__(self, context=None, char_end=None, char_start=None, meta=None):
        self.context = context
        self.char_end = char_end
        self.char_start = char_start
        self.meta = meta

    def __len__(self):
        return self.char_end - self.char_start + 1

    def __eq__(self, other):
        try:
            return self.context == other.context and self.char_start == other.char_start and self.char_end == other.char_end
        except AttributeError:
            return False

    def __ne__(self, other):
        try:
            return self.context != other.context or self.char_start != other.char_start or self.char_end != other.char_end
        except AttributeError:
            return True

    def __hash__(self):
        return hash(self.context) + hash(self.char_start) + hash(self.char_end)

    def get_word_start(self):
        return self.char_to_word_index(self.char_start)

    def get_word_end(self):
        return self.char_to_word_index(self.char_end)

    def get_n(self):
        return self.get_word_end() - self.get_word_start() + 1

    def char_to_word_index(self, ci):
        """Given a character-level index (offset), return the index of the **word this char is in**"""
        i = None
        for i, co in enumerate(self.context.char_offsets):
            if ci == co:
                return i
            elif ci < co:
                return i-1
        return i

    def word_to_char_index(self, wi):
        """Given a word-level index, return the character-level index (offset) of the word's start"""
        return self.context.char_offsets[wi]

    def get_attrib_tokens(self, a):
        """Get the tokens of sentence attribute _a_ over the range defined by word_offset, n"""
        return self.context.__getattribute__(a)[self.get_word_start():self.get_word_end() + 1]

    def get_attrib_span(self, a, sep=" "):
        """Get the span of sentence attribute _a_ over the range defined by word_offset, n"""
        # NOTE: Special behavior for words currently (due to correspondence with char_offsets)
        if a == 'words':
            return self.context.text[self.char_start:self.char_end + 1]
        else:
            return sep.join(self.get_attrib_tokens(a))

    def get_span(self, sep=" "):
        return self.get_attrib_span('words', sep)

    def __contains__(self, other_span):
        return other_span.char_start >= self.char_start and other_span.char_end <= self.char_end

    def __getitem__(self, key):
        """
        Slice operation returns a new candidate sliced according to **char index**
        Note that the slicing is w.r.t. the candidate range (not the abs. sentence char indexing)
        """
        if isinstance(key, slice):
            char_start = self.char_start if key.start is None else self.char_start + key.start
            if key.stop is None:
                char_end = self.char_end
            elif key.stop >= 0:
                char_end = self.char_start + key.stop - 1
            else:
                char_end = self.char_end + key.stop
            return self._get_instance(char_start=char_start, char_end=char_end, context=self.context)
        else:
            raise NotImplementedError()

    def __repr__(self):
        return '%s("%s", context=%s, chars=[%s,%s], words=[%s,%s])' \
            % (self.__class__.__name__, self.get_span(), self.context.id, self.char_start, self.char_end,
               self.get_word_start(), self.get_word_end())

    def _get_instance(self, **kwargs):
        return TemporarySpan(**kwargs)

    def promote(self):
        return Span(context=self.context, char_start=self.char_start, char_end=self.char_end)


class Span(TemporarySpan, Context):
    """
    A span of characters, identified by Context id and character-index start, end (inclusive).

    char_offsets are **relative to the Context start**
    """
    __table__ = 'span'
    id = Column(Integer, ForeignKey('context.id'), primary_key=True)
    parent_id = Column(Integer, ForeignKey('context.id'))
    parent = relationship('Context', backref=backref('spans', cascade='all, delete-orphan'), foreign_keys=parent_id)
    char_start = Column(Integer, nullable=False),
    char_end = Column(Integer, nullable=False),
    meta = Column('meta', PickleType)

    __table_args__ = (
        UniqueConstraint(parent_id, char_start, char_end),
    )

    context = relationship('Context', backref=backref('candidates', cascade_backrefs=False))

    __mapper_args__ = {
        'polymorphic_identity': 'span',
    }

    def _get_instance(self, **kwargs):
        return Span(**kwargs)
